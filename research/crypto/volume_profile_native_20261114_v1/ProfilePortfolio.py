"""Backtest-only source-policy IStrategy with actual native position-state input."""

import numpy as np
import pandas as pd
from ProfileRules import features, FlatState
from freqtrade.strategy import IStrategy, stoploss_from_absolute
from freqtrade.enums import RunMode, ExitType
from freqtrade.strategy.interface import ExitCheckTuple


class ProfilePortfolio(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "15m"
    startup_candle_count = 192
    can_short = True
    minimal_roi = {}
    stoploss = -0.99
    use_custom_stoploss = True
    use_exit_signal = True
    position_adjustment_enable = False
    order_types = {
        "entry": "market",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    kind = None

    def bot_start(self, **kwargs):
        if self.config.get("runmode") != RunMode.BACKTEST:
            raise ValueError("Profile research BACKTEST only, no services")
        if self.config["exchange"]["pair_whitelist"] != ["BTC/USDT:USDT"]:
            raise ValueError("Frozen pair changed")
        raw = self.dp.get_pair_dataframe("BTC/USDT:USDT", "15m", "futures")
        fields = features(raw)
        self.research_features = {"BTC/USDT:USDT": fields}
        self._weight_rows = []
        self.research_trace = {"state": [], "accepted": [], "sizing": [], "fills": []}
        self.research_allocation_diagnostics = {
            "kind": self.kind,
            "clock": "completed15mflat-only native state",
            "independent_source_account": False,
        }
        self._targets = {}
        self._state = FlatState(self.kind)
        self._last_clock = None
        self._pending = None

    def populate_indicators(self, dataframe, metadata):
        f = self.research_features[metadata["pair"]].set_index("date")
        for c in [
            "poc",
            "vah",
            "val",
            "volume_mean",
            "target_weight",
            "source_signal",
            "source_volatility",
        ]:
            dataframe[c] = f[c].reindex(pd.DatetimeIndex(dataframe.date)).to_numpy()
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    def native_profile_step(self, pair, current_time, held, can_enter):
        clock = pd.Timestamp(current_time)
        if self._last_clock == clock:
            return None
        if self._last_clock is not None and clock < self._last_clock:
            raise ValueError("Profile clock moved backwards")
        self._last_clock = clock
        self._pending = None
        frame, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        rows = frame.loc[frame.date + pd.Timedelta(minutes=15) <= clock]
        if rows.empty or pd.Timestamp(rows.date.iloc[-1]) + pd.Timedelta(minutes=15) != clock:
            raise ValueError("Missing completed15mcallback input")
        if clock < pd.Timestamp("2025-01-01T00:00Z"):
            return None
        row = rows.iloc[-1]
        before = {
            "outside": self._state.outside,
            "count": self._state.count,
            "latched": self._state.latched,
        }
        result = None if held else self._state.step(row)
        permitted = bool(can_enter and not (clock.minute == 0 and clock.hour in [0, 8, 16]))
        record = {
            "clock": str(clock),
            "input_date": str(row.date),
            "held_native": held,
            "before": before,
            "after": {
                "outside": self._state.outside,
                "count": self._state.count,
                "latched": self._state.latched,
            },
            "intent": result,
            "clock_permitted": permitted,
        }
        self.research_trace["state"].append(record)
        self._weight_rows.append((clock, 0 if result is None else result["side"]))
        if not result or not permitted:
            return None
        self._pending = {
            **result,
            "clock": clock,
            "input_date": pd.Timestamp(row.date),
            "signal_close": float(row.close),
        }
        self._targets[str(clock)] = {pair: result}
        return "long" if result["side"] == 1 else "short"

    @property
    def research_weights(self):
        return pd.DataFrame(self._weight_rows, columns=["clock", "BTC/USDT:USDT"]).set_index(
            "clock"
        )

    def leverage(self, **kwargs):
        return 3.0

    def _valid_entry(self, clock, rate, side):
        p = self._pending
        if (
            not p
            or p["clock"] != pd.Timestamp(clock)
            or side != ("long" if p["side"] == 1 else "short")
        ):
            return False
        return p["stop"] < rate < p["target"] if side == "long" else p["target"] < rate < p["stop"]

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
        if leverage != 3 or not self._valid_entry(current_time, current_rate, side):
            return 0.0
        cash = self.wallets.get_total("USDT")
        available = self.wallets.get_available_stake_amount()
        distance = abs(current_rate - self._pending["stop"])
        margin = min(0.01 * cash / distance * current_rate / 3, 0.99 * available, max_stake)
        admitted = margin if margin >= (min_stake or 0) else 0.0
        self.research_trace["sizing"].append(
            {
                "clock": str(current_time),
                "native_cash_budget": cash,
                "available_margin": available,
                "stop": self._pending["stop"],
                "rate": current_rate,
                "requested_margin": admitted,
                "leverage": leverage,
            }
        )
        return admitted

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        if not self._valid_entry(current_time, rate, side):
            return False
        self.research_trace["accepted"].append(
            {
                "clock": str(current_time),
                "amount": amount,
                "rate": rate,
                "side": side,
                **{k: self._pending[k] for k in ["stop", "target", "signal_close"]},
            }
        )
        return True

    def order_filled(self, pair, trade, order, current_time, **kwargs):
        if order.ft_order_side == trade.entry_side:
            if not self._pending or self._pending["clock"] != pd.Timestamp(current_time):
                raise ValueError("Entry has no native profile intent")
            for k in ["stop", "target", "signal_close"]:
                trade.set_custom_data(key="profile_" + k, value=float(self._pending[k]))
            self.research_trace["fills"].append(
                {
                    "clock": str(current_time),
                    "order": order.order_id,
                    "side": order.ft_order_side,
                    "amount": order.safe_filled,
                    "price": order.safe_price,
                    **{k: self._pending[k] for k in ["stop", "target"]},
                }
            )

    def custom_stoploss(
        self, pair, trade, current_time, current_rate, current_profit, after_fill=False, **kwargs
    ):
        stop = trade.get_custom_data(key="profile_stop")
        if stop is None:
            return self.stoploss
        return stoploss_from_absolute(
            float(stop), current_rate, is_short=trade.is_short, leverage=trade.leverage
        )

    def should_exit(
        self, trade, rate, current_time, *, enter, exit_, low=None, high=None, force_stoploss=0
    ):
        exits = super().should_exit(
            trade,
            rate,
            current_time,
            enter=enter,
            exit_=exit_,
            low=low,
            high=high,
            force_stoploss=force_stoploss,
        )
        target = trade.get_custom_data(key="profile_target")
        if target is not None and (
            low is not None and low <= target
            if trade.is_short
            else high is not None and high >= target
        ):
            exits.append(ExitCheckTuple(ExitType.CUSTOM_EXIT, "profile_target"))
        return exits

    def custom_exit_price(
        self, pair, trade, current_time, proposed_rate, current_profit, exit_tag, **kwargs
    ):
        return (
            trade.get_custom_data(key="profile_target")
            if exit_tag == "profile_target"
            else proposed_rate
        )


class ProfileBreakout(ProfilePortfolio):
    kind = "breakout"


class ProfileFade(ProfilePortfolio):
    kind = "fade"


class ProfileNoVolume(ProfilePortfolio):
    kind = "no_volume"
