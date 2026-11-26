"""Feature-only native API admission; deliberately refuses entry/exit evaluation."""

import pandas as pd
from WaveletCpuFeatures import forecast

from freqtrade.enums import RunMode
from freqtrade.strategy import IStrategy


class WaveletFeatureProbe(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "15m"
    startup_candle_count = 0
    can_short = False
    minimal_roi = {}
    stoploss = -0.99

    def ft_bot_start(self, **kwargs):
        if (
            self.config.get("runmode") != RunMode.BACKTEST
            or not self.config.get("dry_run")
            or self.config.get("research_wavelet_feature_probe") is not True
        ):
            raise ValueError("Isolated native feature admission only")
        return super().ft_bot_start(**kwargs)

    def populate_indicators(self, dataframe, metadata):
        features, training = forecast(dataframe.set_index("date").close)
        self.feature_training = training
        for column in [
            "source_gain",
            "normalized_gain",
            "source_predicted_gain",
            "model_ready",
            "known_at",
        ]:
            dataframe[column] = features[column].to_numpy()
        expected = pd.DatetimeIndex(dataframe.date) + pd.Timedelta(minutes=15)
        if not pd.DatetimeIndex(dataframe.known_at).as_unit('ns').equals(expected.as_unit('ns')):
            raise ValueError("Feature completion clock differs")
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        raise NotImplementedError("Feature-only admission; source entry/risk policy not integrated")

    def populate_exit_trend(self, dataframe, metadata):
        raise NotImplementedError("Feature-only admission; source exit/risk policy not integrated")
