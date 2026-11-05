"""A fixed funding-carry filter on the existing hourly trend baseline."""

import importlib.util
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, sha
from freqtrade.research.strategies import CryptoTrend


def carry_features(frame: pd.DataFrame, events: pd.DataFrame, factor, start: str, end: str):
    # Freqtrade dates are bar opens; the decision is made after that hour closes.
    clocks = (frame.date + pd.Timedelta(hours=1)).dt.as_unit("ms").astype("int64").tolist()
    records = [
        {
            "symbol": "contract",
            "time_ms": int(pd.Timestamp(row.original_date).value // 1_000_000),
            "rate": float(row.funding_rate),
        }
        for row in events.itertuples()
    ]
    return factor.calculate(
        clocks,
        records,
        int(pd.Timestamp(start, tz="UTC").timestamp() * 1000),
        int(pd.Timestamp(end, tz="UTC").timestamp() * 1000) - 1,
    )


class FundingTrend(CryptoTrend):
    """Keep a trend entry only when past funding has favored that position direction."""

    def populate_indicators(self, dataframe, metadata):
        dataframe = super().populate_indicators(dataframe, metadata)
        dataset = Path(self.config["research_funding_dataset"])
        factor_path = Path(self.config["research_factor_path"])
        if sha(factor_path) != self.config["research_factor_sha256"]:
            raise ValueError("Funding factor implementation changed")
        spec = importlib.util.spec_from_file_location("registered_funding_factor", factor_path)
        factor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(factor)
        symbol = metadata["pair"].split("/")[0] + "USDT"
        events_path = dataset / f"{symbol}_funding_events.parquet"
        if sha(events_path) != read(dataset / "receipts.json")[events_path.name]:
            raise ValueError("Funding events changed after data preparation")
        events = pd.read_parquet(events_path)
        plan = read(dataset / "plan.json")
        features = carry_features(
            dataframe, events, factor, plan["warmup_start"], plan["holdout_end"]
        )
        dataframe["funding_carry_24h"] = features["funding_carry_24h"].to_numpy()
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe = super().populate_entry_trend(dataframe, metadata)
        dataframe.loc[~dataframe.funding_carry_24h.ge(0), "enter_long"] = 0
        dataframe.loc[~dataframe.funding_carry_24h.le(0), "enter_short"] = 0
        return dataframe


class TrendControl(CryptoTrend):
    """Identical baseline, recomputed with the same valuation and runtime as the filter."""
