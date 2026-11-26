"""Local research-only stock DataProvider; native storage, explicit session clocks.

This is not an upstream Freqtrade stock exchange/account implementation. Daily
dates remain labels. A daily OHLCV is visible to callbacks only at its scheduled
close. The inherited crypto provider and its caches are not modified.
"""

from datetime import UTC, datetime

import pandas as pd

from freqtrade.data.dataprovider import MAX_DATAFRAME_CANDLES, DataProvider
from freqtrade.data.history.equity import (
    EquityAdmissionError,
    EquitySessionContract,
    load_equity_pair_history,
)
from freqtrade.enums import CandleType, RunMode


class EquitySchedule:
    """Explicit software-calendar schedule, not exchange certification."""

    def __init__(self, contract: EquitySessionContract, schedule: pd.DataFrame):
        self.contract = contract
        self.frame = schedule.copy()
        self.frame.index = pd.DatetimeIndex(pd.to_datetime(self.frame.index, utc=True))
        if not self.frame.index.equals(contract.index()):
            raise EquityAdmissionError("Schedule/session labels must match exactly")
        for column in ("open", "close", "break_start", "break_end"):
            if column not in self.frame:
                raise EquityAdmissionError("Missing schedule clock")
            self.frame[column] = pd.to_datetime(self.frame[column], utc=True)
        opened, closed = self.frame.open, self.frame.close
        if opened.isna().any() or closed.isna().any() or (opened >= closed).any():
            raise EquityAdmissionError("Invalid open/close clock")
        if not opened.dt.normalize().equals(pd.Series(self.frame.index, index=self.frame.index)):
            raise EquityAdmissionError("Open clock must match session label")
        if not closed.dt.normalize().equals(pd.Series(self.frame.index, index=self.frame.index)):
            raise EquityAdmissionError("Close clock must match session label")
        start, end = self.frame.break_start, self.frame.break_end
        if (start.isna() != end.isna()).any():
            raise EquityAdmissionError("Unpaired lunch-break clocks")
        has_break = start.notna()
        if (
            (opened[has_break] >= start[has_break])
            | (start[has_break] >= end[has_break])
            | (end[has_break] >= closed[has_break])
        ).any():
            raise EquityAdmissionError("Invalid lunch-break clock")
        if len(opened) > 1 and (opened.iloc[1:].to_numpy() <= closed.iloc[:-1].to_numpy()).any():
            raise EquityAdmissionError("Overlapping sessions")

    def visible(self, frame: pd.DataFrame, at: datetime) -> pd.DataFrame:
        stamp = pd.Timestamp(at)
        if stamp.tzinfo is None:
            raise EquityAdmissionError("Timezone-aware observation clock required")
        dates = pd.DatetimeIndex(pd.to_datetime(frame.date, utc=True))
        if dates.has_duplicates or not dates.is_monotonic_increasing:
            raise EquityAdmissionError("Duplicate/unordered analyzed session labels")
        if not dates.isin(self.frame.index).all():
            raise EquityAdmissionError("Unknown analyzed session label")
        return frame.loc[self.frame.close.reindex(dates).to_numpy() <= stamp].copy()


class EquityDataProvider(DataProvider):
    """Native DataProvider extension for vectorized and as-of stock research.

    historic_ohlcv exposes the complete validated frame for native vectorized
    indicators. Callback methods require an explicit as-of clock. This class
    does not authorize a financial backtest or live/dry-run execution.
    """

    def __init__(self, config, schedules: dict[str, EquitySchedule]):
        if RunMode(config.get("runmode", RunMode.OTHER)) != RunMode.BACKTEST:
            raise EquityAdmissionError("Equity provider is backtest research only")
        if config.get("timeframe") != "1d" or not schedules:
            raise EquityAdmissionError("Explicit daily stock schedules required")
        super().__init__(config, None)
        self.schedules = schedules
        self._equity_raw = {}
        self._equity_analyzed = {}
        self._equity_asof = None

    def _check(self, pair, timeframe, candle_type=""):
        if (
            pair not in self.schedules
            or timeframe != "1d"
            or candle_type not in ("", CandleType.SPOT, "spot")
        ):
            raise EquityAdmissionError("Undeclared stock/timeframe/candle type")

    def historic_ohlcv(self, pair, timeframe, candle_type=""):
        self._check(pair, timeframe, candle_type)
        if pair not in self._equity_raw:
            self._equity_raw[pair] = load_equity_pair_history(
                self._config["datadir"], self.schedules[pair].contract
            )
        return self._equity_raw[pair].copy()

    def _set_dataframe_max_date(self, limit_date):
        if pd.Timestamp(limit_date).tzinfo is None:
            raise EquityAdmissionError("Timezone-aware observation clock required")
        self._equity_asof = limit_date
        super()._set_dataframe_max_date(limit_date)

    def get_pair_dataframe(self, pair, timeframe=None, candle_type=""):
        if self._equity_asof is None:
            raise EquityAdmissionError("Explicit as-of clock required for callbacks")
        frame = self.historic_ohlcv(pair, timeframe or "1d", candle_type)
        return self.schedules[pair].visible(frame, self._equity_asof)

    def _set_cached_df(self, pair, timeframe, dataframe, candle_type):
        self._check(pair, timeframe, candle_type)
        # Validate dates before accepting a cache; an early as-of does not repair
        # unknown labels, reordered input, or a fabricated weekend row.
        self.schedules[pair].visible(dataframe, datetime.max.replace(tzinfo=UTC))
        self._equity_analyzed[pair] = dataframe.copy()
        super()._set_cached_df(pair, timeframe, dataframe, candle_type)

    def get_analyzed_dataframe(self, pair, timeframe):
        self._check(pair, timeframe)
        if self._equity_asof is None:
            raise EquityAdmissionError("Explicit as-of clock required for callbacks")
        if pair not in self._equity_analyzed:
            return pd.DataFrame(), datetime.fromtimestamp(0, tz=UTC)
        visible = (
            self.schedules[pair]
            .visible(self._equity_analyzed[pair], self._equity_asof)
            .tail(MAX_DATAFRAME_CANDLES)
        )
        clock = (
            self.schedules[pair].frame.loc[pd.Timestamp(visible.iloc[-1].date), "close"]
            if len(visible)
            else datetime.fromtimestamp(0, tz=UTC)
        )
        return visible, clock

    def clear_cache(self):
        super().clear_cache()
        self._equity_analyzed = {}
        self._equity_asof = None

    def current_whitelist(self):
        return list(self.schedules)
