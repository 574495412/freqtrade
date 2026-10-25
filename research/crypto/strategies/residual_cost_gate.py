"""Frozen chronological forecast gates; account risk and exits stay unchanged."""

from pathlib import Path

import pandas as pd
from btc_residual import BTCResidualRaw

from freqtrade.research.data import sha


class ResidualOpportunityControl(BTCResidualRaw):
    require_positive_forecast = False
    require_high_horizon_probability = False

    def populate_indicators(self, dataframe, metadata):
        frame = super().populate_indicators(dataframe, metadata)
        symbol = metadata["pair"].split("/")[0] + "USDT"
        path = Path(self.config["research_entry_predictions"]) / f"{symbol}.parquet"
        if sha(path) != self.config["research_entry_prediction_hashes"][symbol]:
            raise ValueError("Frozen forecast identity changed")
        return frame.merge(pd.read_parquet(path), on="date", how="left", validate="one_to_one")

    def populate_entry_trend(self, dataframe, metadata):
        frame = dataframe.copy()
        accept = frame.model_ready.eq(True)
        if self.require_positive_forecast:
            accept &= frame.predicted_net_horizon_return.gt(0)
        if self.require_high_horizon_probability:
            accept &= frame.predicted_horizon_positive_probability.ge(0.8)
        frame["enter_long"] = (accept & frame.opportunity_side.eq(1)).astype(int)
        frame["enter_short"] = (accept & frame.opportunity_side.eq(-1)).astype(int)
        frame["enter_tag"] = ""
        frame.loc[accept & frame.opportunity_sleeve.eq("small"), "enter_tag"] = "small_residual"
        frame.loc[accept & frame.opportunity_sleeve.eq("core"), "enter_tag"] = "core_residual"
        return frame


class ResidualRidgeNet(ResidualOpportunityControl):
    require_positive_forecast = True


class ResidualWinNet(ResidualRidgeNet):
    require_high_horizon_probability = True
