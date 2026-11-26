"""Local six-major transfer of public liquidity-direction hypotheses.

The public strategy's secret filter/full-market results are not reproduced.
Native sizing/fills own all cash and quantities; ranks carry until changed.
"""

import hashlib
from pathlib import Path

import pandas as pd
from SpreadFeatures import calculate_features, targets

from freqtrade.strategy import IStrategy


class SpreadPortfolio(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    startup_candle_count = 72
    can_short = True
    minimal_roi = {}
    stoploss = -0.99
    trailing_stop = False
    position_adjustment_enable = False
    use_exit_signal = True
    order_types = {
        "entry": "market",
        "exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    factor_field = None

    def bot_start(self, **kwargs):
        path = Path(self.config["research_quote_volume"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != self.config["research_quote_sha256"]:
            raise ValueError("Raw quote-volume input drift")
        quote = pd.read_parquet(path)
        self.research_features = {}
        self.research_trace = {"attempts": [], "accepted": []}
        pairs = self.config["exchange"]["pair_whitelist"]
        for pair in pairs:
            daily = self.dp.get_pair_dataframe(pair, "1d", "futures")
            dollars = quote.loc[quote.pair.eq(pair)].set_index("date").quote_volume
            self.research_features[pair] = calculate_features(daily, dollars)
        self._targets = targets(self.research_features, self.factor_field, pairs)
        self._budgets = {}

    def populate_indicators(self, dataframe, metadata):
        pair = metadata["pair"]
        days = dataframe.date.dt.strftime("%Y-%m-%d")
        dataframe["daily_target"] = days.map(lambda day: self._targets.get(day, {}).get(pair, 0))
        scores = self.research_features[pair].copy()
        scores["day"] = scores.decision_date.dt.strftime("%Y-%m-%d")
        score = scores.set_index("day")[self.factor_field]
        dataframe["factor_value"] = days.map(score)
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        pulse = dataframe.date.dt.hour.eq(0)
        dataframe["enter_long"] = (pulse & dataframe.daily_target.eq(1)).astype(int)
        dataframe["enter_short"] = (pulse & dataframe.daily_target.eq(-1)).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        pulse = dataframe.date.dt.hour.eq(0)
        dataframe["exit_long"] = (pulse & dataframe.daily_target.ne(1)).astype(int)
        dataframe["exit_short"] = (pulse & dataframe.daily_target.ne(-1)).astype(int)
        dataframe["exit_tag"] = "daily_rank_change"
        return dataframe

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
        stamp = pd.Timestamp(current_time)
        if stamp.hour != 1 or stamp.minute or stamp.second:
            return 0
        budget = min(proposed_stake, max_stake)
        self._budgets[(pair, stamp.isoformat())] = budget
        self.research_trace["attempts"].append(
            {
                "pair": pair,
                "clock": stamp.isoformat(),
                "proposed_stake": proposed_stake,
                "budget": budget,
                "minimum": min_stake,
                "side": side,
            }
        )
        return budget if budget >= (min_stake or 0) else 0

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        stamp = pd.Timestamp(current_time)
        budget = self._budgets.get((pair, stamp.isoformat()), 0)
        if stamp.hour != 1 or stamp.minute or stamp.second or amount * rate > budget + 0.01:
            return False
        self.research_trace["accepted"].append(
            {
                "pair": pair,
                "clock": stamp.isoformat(),
                "amount": amount,
                "rate": rate,
                "budget": budget,
                "side": side,
            }
        )
        return True


class EdgeLiquidity(SpreadPortfolio):
    factor_field = "edge30"


class AmihudLiquidity(SpreadPortfolio):
    factor_field = "amihud30"


class LowVolBaseline(SpreadPortfolio):
    factor_field = "lowvol63"
