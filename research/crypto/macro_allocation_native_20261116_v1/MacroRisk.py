"""Local native execution of causal Cover weights; no third-party code reused."""

import numpy as np
import pandas as pd

from NativeAllocations import marked_budget
from MacroInputs import MacroClock, macro_headwind_at, parse_yahoo_macro
import json
from pathlib import Path
from datetime import datetime
from freqtrade.data.history import load_pair_history
from freqtrade.enums import CandleType, RunMode
from freqtrade.persistence import LocalTrade, Trade
from freqtrade.strategy import IStrategy


class MacroRisk(IStrategy):
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
    allocation_mode = "macro"
    daily = True

    def ft_bot_start(self, **kwargs):
        if (
            self.config.get("runmode") != RunMode.BACKTEST
            or not self.config.get("dry_run")
            or self.config.get("research_macro_allocation") is not True
            or self.config.get("trading_mode") != "spot"
            or self.config["exchange"]["pair_whitelist"] != ["BTC/USDT", "ETH/USDT"]
            or self.config.get("max_open_trades") != 2
            or self.config.get("macro_input_clock") != "next_day_06utc_model"
            or Trade.use_db
        ):
            raise ValueError("Isolated fixed BTC/ETH/CASH native research required")
        self._fatal_error, self._context, self._attempted = None, None, set()
        self.research_trace = {"budgets": [], "attempts": [], "accepted": [], "adjustments": []}
        pairs = self.config["exchange"]["pair_whitelist"]
        self._prices = {}
        for pair in pairs:
            hourly = load_pair_history(datadir=self.config['datadir'],timeframe='1h',pair=pair,
                candle_type=CandleType.SPOT,fill_up_missing=False)
            self._prices[pair] = hourly.set_index('date').open
        records = json.loads(Path(self.config['macro_source_receipts']).read_text())
        snapshots = [parse_yahoo_macro(Path(row['path']).read_bytes(),symbol=symbol,
            observed_at=datetime.fromisoformat(row['observed_at']))
            for row,symbol in zip(records,['^TNX','DX-Y.NYB'],strict=True)]
        start = pd.Timestamp('2025-01-01T00:00Z')
        clocks = [start]
        if self.daily:
            clocks += list(pd.date_range('2025-01-01T06:00Z','2026-08-31T06:00Z',freq='D'))
        self.research_weights = {}
        self.research_trace['policy'] = []
        for stamp in clocks:
            fraction = 1.
            status = 'not_used_control'
            if self.allocation_mode == 'macro':
                state = macro_headwind_at(*snapshots,at=stamp,clock=MacroClock.NEXT_DAY_06UTC_MODEL)
                if state['status'] != 'MODEL_ONLY' or state['target_fraction'] is None:
                    raise ValueError('Unadmitted conditional macro state')
                fraction, status = state['target_fraction'], state['status']
            self.research_weights[stamp] = {'BTC/USDT':fraction,'ETH/USDT':0.}
            self.research_trace['policy'].append(dict(clock=str(stamp),fraction=fraction,status=status))
        return super().ft_bot_start(**kwargs)

    def populate_indicators(self, dataframe, metadata):
        clocks = pd.DatetimeIndex(dataframe.date) + pd.Timedelta(hours=1)
        dataframe['entry_schedule'] = clocks.isin(self.research_weights)
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe['enter_long'] = (dataframe.entry_schedule & (metadata['pair']=='BTC/USDT')).astype(int)
        dataframe['enter_short'] = 0
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    def context(self, current_time):
        stamp = pd.Timestamp(current_time)
        if stamp not in self.research_weights:
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
                weight = float(self.research_weights[stamp][pair])
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
        self.position_adjustment_enable = self.daily and pd.Timestamp(current_time) in self.research_weights
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
        return stake, "macro_daily_actual_quantity"


class DailyFullBTC(MacroRisk):
    allocation_mode = 'full'


class BuyHoldBTC(DailyFullBTC):
    daily = False
