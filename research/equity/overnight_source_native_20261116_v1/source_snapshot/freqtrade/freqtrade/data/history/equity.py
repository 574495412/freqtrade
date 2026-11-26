"""Local equity session admission through Freqtrade's native history API.

Daily timestamps are session labels, never execution timestamps. This module
does not implement a stock account, settlement, dividends, borrow, or matching.
The ordinary Backtesting/DataProvider loaders are deliberately unchanged.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
from freqtrade.data.history.history_utils import load_pair_history
from freqtrade.enums import CandleType


class EquityAdmissionError(ValueError):
    """An input cannot support the declared complete equity window."""


@dataclass(frozen=True)
class EquitySessionContract:
    pair: str
    calendar: str
    sessions: tuple[str, ...]

    def index(self) -> pd.DatetimeIndex:
        if self.calendar not in {"XNYS", "XHKG"} or not self.sessions:
            raise EquityAdmissionError("Explicit XNYS/XHKG complete session grid required")
        dates = pd.DatetimeIndex(pd.to_datetime(self.sessions, utc=True))
        if dates.has_duplicates or not dates.is_monotonic_increasing:
            raise EquityAdmissionError("Session grid must be unique and increasing")
        if not dates.equals(dates.normalize()):
            raise EquityAdmissionError("Daily session labels must be UTC midnight")
        return dates

    def validate(self, frame: pd.DataFrame) -> None:
        expected = self.index()
        columns = ["date", "open", "high", "low", "close", "volume"]
        if not set(columns).issubset(frame.columns) or frame.empty:
            raise EquityAdmissionError("Missing daily OHLCV")
        actual = pd.DatetimeIndex(pd.to_datetime(frame.date, utc=True))
        if actual.has_duplicates or not actual.is_monotonic_increasing:
            raise EquityAdmissionError(
                "Duplicate/unordered source sessions; no aggregation allowed"
            )
        if not actual.equals(expected):
            missing = expected.difference(actual).strftime("%Y-%m-%d").tolist()
            extra = actual.difference(expected).strftime("%Y-%m-%d").tolist()
            raise EquityAdmissionError(
                f"Session mismatch: missing={missing[:5]}, extra={extra[:5]}"
            )
        values = frame[columns[1:]].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values[:, :4] <= 0).any():
            raise EquityAdmissionError("Nonfinite/nonpositive OHLCV")
        if (values[:, 4] <= 0).any():
            raise EquityAdmissionError("Zero/negative-volume session needs explicit halt semantics")
        if (frame.low > frame[["open", "close"]].min(axis=1)).any() or (
            frame.high < frame[["open", "close"]].max(axis=1)
        ).any():
            raise EquityAdmissionError("OHLC range conflict")


class EquitySessionFeatherHandler(FeatherDataHandler):
    """Validate raw native rows before native duplicate aggregation can hide them."""

    def __init__(self, datadir: Path, contract: EquitySessionContract):
        super().__init__(datadir)
        self.contract = contract

    def _ohlcv_load(self, pair, timeframe, timerange, candle_type):
        if (
            pair != self.contract.pair
            or timeframe != "1d"
            or candle_type != CandleType.SPOT
            or timerange is not None
        ):
            raise EquityAdmissionError("Only the exact full-window daily equity input is supported")
        frame = super()._ohlcv_load(pair, timeframe, timerange, candle_type)
        self.contract.validate(frame)
        return frame


def load_equity_pair_history(datadir: Path, contract: EquitySessionContract) -> pd.DataFrame:
    """Native loader/storage, explicit no-fill, strict full-window session checks."""
    result = load_pair_history(
        pair=contract.pair,
        timeframe="1d",
        datadir=datadir,
        fill_up_missing=False,
        drop_incomplete=False,
        startup_candles=0,
        data_handler=EquitySessionFeatherHandler(datadir, contract),
        candle_type=CandleType.SPOT,
    )
    contract.validate(result)
    return result


def require_complete_equity_account_support() -> None:
    """Fail closed until the owning native account integration actually exists."""
    raise EquityAdmissionError(
        "History admission only: native equity execution clocks, quantity/lot/tick rules, "
        "cash settlement, side-specific fees/taxes, dividend entitlements/payment, "
        "corporate actions and (for shorts) borrow/recall are not integrated. "
        "No complete equity account return is certified."
    )
