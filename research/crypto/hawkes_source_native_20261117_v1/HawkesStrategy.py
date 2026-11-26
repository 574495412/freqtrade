"""Local native-account adapter of the unchanged author signed Hawkes state."""

from HawkesInputs import build_features
from freqtrade.strategy import IStrategy


class HawkesStrategy(IStrategy):
    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "1h"
    # Fixed common data origin2023-08-01; native_start2024-12-31T23:00.
    startup_candle_count = 12455
    process_only_new_candles = True
    minimal_roi = {}
    stoploss = -0.99
    trailing_stop = False
    position_adjustment_enable = False
    use_exit_signal = True
    exit_profit_only = False
    order_types = {
        "entry": "market", "exit": "market", "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    def bot_start(self, **kwargs):
        if (not self.config.get("research_hawkes") or self.config.get("dry_run") is not True
                or self.config.get("trading_mode") != "futures"):
            raise ValueError("Fixed isolated historical Hawkes research only")

    def populate_indicators(self, dataframe, metadata):
        values = build_features(dataframe)
        for name in values:
            dataframe["hawkes_" + name] = values[name]
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe["enter_long"] = dataframe.hawkes_signal.eq(1).astype(int)
        dataframe["enter_short"] = dataframe.hawkes_signal.eq(-1).astype(int)
        dataframe["enter_tag"] = "source_hawkes_state"
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = dataframe.hawkes_signal.ne(1).astype(int)
        dataframe["exit_short"] = dataframe.hawkes_signal.ne(-1).astype(int)
        dataframe["exit_tag"] = "source_hawkes_state_change"
        return dataframe

    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, entry_tag, side, **kwargs):
        return 1.0
