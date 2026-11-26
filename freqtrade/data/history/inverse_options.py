"""Inverse-option chart admission through native history storage, never quotes.

The SPOT CandleType below is only the existing OHLCV storage namespace. It does
not declare these instruments spot tradable or enable an option account. Zero
source volumes remain zero; unchanged prices are not evidence of liquidity.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd

from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
from freqtrade.data.history.history_utils import load_pair_history
from freqtrade.data.inverse_option_contracts import (
    InverseOptionSpec,
    OptionAdmissionError,
    aware,
    read_response,
)
from freqtrade.enums import CandleType


@dataclass(frozen=True)
class OptionChartContract:
    spec: InverseOptionSpec
    hours: tuple[datetime, ...]

    def index(self):
        if not isinstance(self.spec, InverseOptionSpec) or not isinstance(self.hours, tuple):
            raise OptionAdmissionError("Explicit admitted option and immutable hour grid required")
        if len(self.hours) < 2:
            raise OptionAdmissionError("Explicit complete multi-hour chart window required")
        for stamp in self.hours:
            aware(stamp)
        index = pd.DatetimeIndex(pd.to_datetime(self.hours, utc=True))
        if index.has_duplicates or not index.is_monotonic_increasing:
            raise OptionAdmissionError("Unique increasing option chart grid required")
        if not index.equals(index.floor("h")) or any(
            b - a != timedelta(hours=1) for a, b in zip(self.hours, self.hours[1:], strict=False)
        ):
            raise OptionAdmissionError("Only a complete exact hourly grid is supported")
        if (
            index[0] < self.spec.creation_at
            or index[-1] + timedelta(hours=1) > self.spec.expiration_at
        ):
            raise OptionAdmissionError("Chart window crosses option creation/expiry")
        return index

    def validate(self, frame):
        expected = self.index()
        columns = ["date", "open", "high", "low", "close", "volume"]
        if frame.empty or not set(columns) <= set(frame.columns):
            raise OptionAdmissionError("Complete option chart OHLCV required")
        actual = pd.DatetimeIndex(pd.to_datetime(frame.date, utc=True))
        if not actual.equals(expected):
            raise OptionAdmissionError("Raw option grid mismatch;no deduplication or gap filling")
        values = frame[columns[1:]].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values < 0).any():
            raise OptionAdmissionError("Finite nonnegative premium prices/volume required")
        if (frame.low > frame[["open", "close"]].min(axis=1)).any() or (
            frame.high < frame[["open", "close"]].max(axis=1)
        ).any():
            raise OptionAdmissionError("Original option OHLC conflict")


def parse_option_chart(raw, *, source_url, http_status, contract):
    if not isinstance(contract, OptionChartContract):
        raise OptionAdmissionError("Exact admitted option chart contract required")
    index = contract.index()
    result, query = read_response(
        raw, source_url=source_url, http_status=http_status, endpoint="get_tradingview_chart_data"
    )
    if (
        query.get("instrument_name") != [contract.spec.instrument_name]
        or query.get("resolution") != ["60"]
        or query.get("start_timestamp") != [str(int(index[0].timestamp() * 1000))]
        or query.get("end_timestamp") != [str(int(index[-1].timestamp() * 1000))]
    ):
        raise OptionAdmissionError("Exact original option/request window required")
    if not isinstance(result, dict) or result.get("status") != "ok":
        raise OptionAdmissionError("No-data/error response cannot certify full option window")
    keys = ["ticks", "open", "high", "low", "close", "volume", "cost"]
    if any(not isinstance(result.get(k), list) or len(result[k]) != len(index) for k in keys):
        raise OptionAdmissionError("Equal complete original option chart arrays required")
    if any(type(x) is not int for x in result["ticks"]):
        raise OptionAdmissionError("Original integer millisecond chart ticks required")
    if any(
        isinstance(x, bool) or not isinstance(x, (int, Decimal))
        for key in keys[1:]
        for x in result[key]
    ):
        raise OptionAdmissionError("Original numeric premium/volume fields required")
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(result["ticks"], unit="ms", utc=True),
            **{k: [float(x) for x in result[k]] for k in keys[1:6]},
        }
    )
    contract.validate(frame)
    # Cost is original quote-currency volume,not a strategy fee. Preserve raw
    # source bytes separately;it is not repurposed as account costs or bid/ask.
    cost = np.asarray(result["cost"], dtype=float)
    if not np.isfinite(cost).all() or (cost < 0).any():
        raise OptionAdmissionError("Invalid original quote-volume cost field")
    return frame


class OptionChartFeatherHandler(FeatherDataHandler):
    def __init__(self, datadir, contract):
        super().__init__(datadir)
        self.contract = contract

    def _ohlcv_load(self, pair, timeframe, timerange, candle_type):
        if (
            pair != self.contract.spec.storage_pair
            or timeframe != "1h"
            or timerange is not None
            or candle_type != CandleType.SPOT
        ):
            raise OptionAdmissionError("Only the exact full-window option chart is admitted")
        frame = super()._ohlcv_load(pair, timeframe, timerange, candle_type)
        self.contract.validate(frame)
        return frame


def load_option_chart_history(datadir, contract):
    result = load_pair_history(
        pair=contract.spec.storage_pair,
        timeframe="1h",
        datadir=datadir,
        fill_up_missing=False,
        drop_incomplete=False,
        startup_candles=0,
        data_handler=OptionChartFeatherHandler(datadir, contract),
        candle_type=CandleType.SPOT,
    )
    contract.validate(result)
    return result
