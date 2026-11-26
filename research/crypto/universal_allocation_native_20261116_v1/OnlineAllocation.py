"""Local native execution of causal Cover weights; no third-party code reused."""

import numpy as np
import pandas as pd

from NativeAllocations import marked_budget
from UniversalSignals import cover_allocation_weights
from freqtrade.data.history import load_pair_history
from freqtrade.enums import CandleType, RunMode
from freqtrade.persistence import LocalTrade, Trade
from freqtrade.strategy import IStrategy


class OnlineAllocation(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    startup_candle_count = 0
    can_short = False
    minimal_roi = {}
    stoploss = -0.99
    use_exit_signal = True
    position_adjustment_enable = True
    max_entry_position_adjustment = -1
    order_types = {
        "entry": "market",
        "exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    allocation_mode = "cover"

    def ft_bot_start(self, **kwargs):
        if (
            self.config.get("runmode") != RunMode.BACKTEST
            or not self.config.get("dry_run")
            or self.config.get("research_universal_allocation") is not True
            or self.config.get("trading_mode") != "spot"
            or self.config["exchange"]["pair_whitelist"] != ["BTC/USDT", "ETH/USDT"]
            or self.config.get("max_open_trades") != 2
            or Trade.use_db
        ):
            raise ValueError("Isolated fixed BTC/ETH/CASH native research required")
        self._fatal_error, self._context, self._attempted = None, None, set()
        self.research_trace = {"budgets": [], "attempts": [], "accepted": [], "adjustments": []}
        pairs = self.config["exchange"]["pair_whitelist"]
        prices, self._prices = {}, {}
        for pair in pairs:
            daily = load_pair_history(
                datadir=self.config["datadir"],
                timeframe="1d",
                pair=pair,
                candle_type=CandleType.SPOT,
            )
            prices[pair] = daily.set_index("date").close
            hourly = load_pair_history(
                datadir=self.config["datadir"],
                timeframe="1h",
                pair=pair,
                candle_type=CandleType.SPOT,
            )
            self._prices[pair] = hourly.set_index("date").open
        days = pd.date_range(
            self.config["allocation_start_day"], self.config["allocation_end_day"], freq="D"
        )
        self.research_weights = cover_allocation_weights(pd.DataFrame(prices), days, resolution=100)
        if self.allocation_mode == "constant":
            self.research_weights[:] = 1.0 / 3
        self.research_trace["weights"] = [
            dict(day=str(day), **row.to_dict()) for day, row in self.research_weights.iterrows()
        ]
        return super().ft_bot_start(**kwargs)

    def populate_indicators(self, dataframe, metadata):
        days = pd.DatetimeIndex(dataframe.date.dt.floor("D"))
        dataframe["target_weight"] = (
            self.research_weights[metadata["pair"]].reindex(days).to_numpy()
        )
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe["enter_long"] = (
            dataframe.date.dt.hour.eq(0) & dataframe.target_weight.gt(0)
        ).astype(int)
        dataframe["enter_short"] = 0
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    def context(self, current_time):
        stamp = pd.Timestamp(current_time)
        if stamp.hour != 1 or stamp.minute or stamp.second:
            return None
        if self._context is not None and self._context["clock"] == stamp:
            return self._context
        try:
            trades = Trade.get_trades_proxy(is_open=True)
            if any(t.is_short or t.leverage != 1 for t in trades):
                raise ValueError("Only actual unlevered spot holdings permitted")
            marks = {p: float(s.loc[stamp]) for p, s in self._prices.items()}
            equity = marked_budget(
                float(self.config["dry_run_wallet"]), LocalTrade.bt_total_profit, trades, marks
            )
            quantities, minimums = {}, {}
            for pair, rate in marks.items():
                weight = float(self.research_weights.at[stamp.floor("D"), pair])
                if not np.isfinite(rate) or rate <= 0 or not 0 <= weight <= 1:
                    raise ValueError("Invalid current open or past-only weight")
                quantity = self.dp._exchange.amount_to_precision(
                    pair, 0.99 * equity * weight / rate
                )
                minimum = (
                    self.dp._exchange.get_min_pair_stake_amount(pair, rate, self.stoploss, 1.0)
                    or 0.0
                )
                if quantity * rate < minimum:
                    quantity = 0.0
                quantities[pair], minimums[pair] = quantity, minimum
            self._context = dict(
                clock=stamp, equity=equity, quantities=quantities, prices=marks, minimums=minimums
            )
            self.research_trace["budgets"].append(
                dict(
                    clock=str(stamp),
                    native_marked_equity=equity,
                    target_quantities=quantities,
                    prices=marks,
                )
            )
            return self._context
        except Exception as exc:
            self._fatal_error = f"{type(exc).__name__}: {exc}"
            raise

    def bot_loop_start(self, current_time, **kwargs):
        self.position_adjustment_enable = pd.Timestamp(current_time).hour == 1
        self.context(current_time)

    def custom_stake_amount(
        self,
        pair,
        current_time,
        current_rate,
        proposed_stake,
        min_stake,
        max_stake,
        leverage,
        entry_tag,
        side,
        **kwargs,
    ):
        context = self.context(current_time)
        if context is None or side != "long" or leverage != 1:
            return 0.0
        budget = min(context["quantities"][pair] * current_rate, max_stake)
        self.research_trace["attempts"].append(
            dict(
                pair=pair,
                clock=str(current_time),
                budget=budget,
                minimum=min_stake,
                maximum=max_stake,
            )
        )
        return budget if budget >= (min_stake or 0) else 0.0

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        context = self.context(current_time)
        if context is None or side != "long":
            return False
        held = sum(t.amount for t in Trade.get_trades_proxy(is_open=True) if t.pair == pair)
        accepted = held + amount <= context["quantities"][pair] + 1e-10
        self.research_trace["accepted"].append(
            dict(
                pair=pair,
                clock=str(current_time),
                accepted=accepted,
                prior_quantity=held,
                amount=amount,
                target_quantity=context["quantities"][pair],
            )
        )
        return accepted

    def adjust_trade_position(
        self, trade, current_time, current_rate, current_profit, min_stake, max_stake, **kwargs
    ):
        context = self.context(current_time)
        if context is None or (trade.id, context["clock"]) in self._attempted:
            return None
        self._attempted.add((trade.id, context["clock"]))
        target = context["quantities"][trade.pair]
        delta = target - trade.amount
        if abs(delta) < 1e-12:
            return None
        if target == 0:
            stake = -trade.stake_amount
        elif delta < 0:
            stake = delta * trade.stake_amount / trade.amount
        else:
            stake = min(delta * current_rate, max_stake)
        if stake > 0 and stake < (min_stake or 0):
            return None
        if stake < 0 and target > 0 and abs(stake) < context["minimums"][trade.pair]:
            return None
        self.research_trace["adjustments"].append(
            dict(
                pair=trade.pair,
                clock=str(current_time),
                prior_quantity=trade.amount,
                target_quantity=target,
                requested_stake_delta=stake,
            )
        )
        return stake, "cover_daily_actual_quantity"


class ConstantThirds(OnlineAllocation):
    allocation_mode = "constant"
