"""Session labels must not leak uncompleted stock bars into callbacks."""

from copy import deepcopy

import pandas as pd
import pytest

from freqtrade.data.equity_provider import EquityDataProvider, EquitySchedule
from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
from freqtrade.data.history.equity import EquityAdmissionError, EquitySessionContract
from freqtrade.enums import CandleType, RunMode


def setup_provider(tmp_path, market="XNYS"):
    days = ("2025-07-02", "2025-07-03", "2025-07-07")
    pair = "TEST/USD" if market == "XNYS" else "TEST/HKD"
    dates = pd.to_datetime(days, utc=True)
    opens = [d + pd.Timedelta(hours=13, minutes=30) for d in dates]
    closes = [
        dates[0] + pd.Timedelta(hours=20),
        dates[1] + pd.Timedelta(hours=17),
        dates[2] + pd.Timedelta(hours=20),
    ]
    schedule = pd.DataFrame(
        {
            "open": opens,
            "close": closes,
            "break_start": pd.DatetimeIndex([pd.NaT] * 3, tz="UTC"),
            "break_end": pd.DatetimeIndex([pd.NaT] * 3, tz="UTC"),
        },
        index=dates,
    )
    if market == "XHKG":
        schedule["open"] = dates + pd.Timedelta(hours=1, minutes=30)
        schedule["close"] = dates + pd.Timedelta(hours=8)
        schedule["break_start"] = dates + pd.Timedelta(hours=4)
        schedule["break_end"] = dates + pd.Timedelta(hours=5)
    contract = EquitySessionContract(pair, market, days)
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": [10.0, 11.0, 12.0],
            "high": [11.0, 12.0, 13.0],
            "low": [9.0, 10.0, 11.0],
            "close": [10.5, 11.5, 12.5],
            "volume": [100.0, 100.0, 100.0],
        }
    )
    FeatherDataHandler(tmp_path).ohlcv_store(pair, "1d", frame, CandleType.SPOT)
    cfg = {
        "runmode": RunMode.BACKTEST,
        "timeframe": "1d",
        "datadir": tmp_path,
        "candle_type_def": CandleType.SPOT,
    }
    provider = EquityDataProvider(cfg, {pair: EquitySchedule(contract, schedule)})
    return provider, pair, frame, schedule, contract


@pytest.mark.parametrize("market", ["XNYS", "XHKG"])
def test_close_boundary_and_lunch(tmp_path, market):
    dp, pair, frame, schedule, _ = setup_provider(tmp_path, market)
    # Native vectorized history is complete, with no weekend/holiday rows.
    pd.testing.assert_frame_equal(
        dp.historic_ohlcv(pair, "1d"), frame, check_dtype=False, check_exact=True
    )
    analyzed = frame.assign(proof=frame.close * 7)
    dp._set_cached_df(pair, "1d", analyzed, CandleType.SPOT)
    for i in range(len(frame)):
        for when, expected in [
            (schedule.open.iloc[i], i),
            (schedule.close.iloc[i] - pd.Timedelta(nanoseconds=1), i),
            (schedule.close.iloc[i], i + 1),
        ]:
            dp._set_dataframe_max_date(when)
            assert len(dp.get_pair_dataframe(pair)) == expected
            cached, refresh = dp.get_analyzed_dataframe(pair, "1d")
            assert len(cached) == expected
            if expected:
                assert refresh == schedule.close.iloc[expected - 1]
                assert cached.proof.iloc[-1] == frame.close.iloc[expected - 1] * 7
        if market == "XHKG":
            dp._set_dataframe_max_date(schedule.break_end.iloc[i])
            assert len(dp.get_pair_dataframe(pair)) == i
    # A holiday is not a synthetic bar, and a half-day closes at 17:00 UTC.
    dp._set_dataframe_max_date(pd.Timestamp("2025-07-06T23:59Z"))
    assert len(dp.get_pair_dataframe(pair)) == 2
    result = dp.get_pair_dataframe(pair)
    result.loc[:, "close"] = 999
    assert dp.get_pair_dataframe(pair).close.iloc[-1] == 11.5


def test_explicit_clock_cache_reset_and_funding_rejection(tmp_path):
    dp, pair, frame, _, _ = setup_provider(tmp_path)
    with pytest.raises(EquityAdmissionError, match="as-of"):
        dp.get_pair_dataframe(pair)
    with pytest.raises(EquityAdmissionError, match="as-of"):
        dp.get_analyzed_dataframe(pair, "1d")
    with pytest.raises(EquityAdmissionError, match="Timezone"):
        dp._set_dataframe_max_date(pd.Timestamp("2025-07-03"))
    with pytest.raises(EquityAdmissionError, match="Undeclared"):
        dp.historic_ohlcv(pair, "1d", "funding_rate")
    for bad in [
        frame.iloc[::-1],
        pd.concat([frame, frame.iloc[[0]]]),
        frame.assign(date=pd.to_datetime(["2025-07-02", "2025-07-03", "2025-07-06"], utc=True)),
    ]:
        with pytest.raises(EquityAdmissionError):
            dp._set_cached_df(pair, "1d", bad, CandleType.SPOT)
    dp._set_dataframe_max_date(pd.Timestamp("2025-07-03T20:00Z"))
    dp.clear_cache()
    with pytest.raises(EquityAdmissionError, match="as-of"):
        dp.get_pair_dataframe(pair)


@pytest.mark.parametrize(
    "kind", ["missing", "reordered", "clock", "break", "next_day", "naive_open", "naive_close"]
)
def test_invalid_schedule(tmp_path, kind):
    _, _, _, schedule, contract = setup_provider(tmp_path)
    bad = deepcopy(schedule)
    if kind == "missing":
        bad = bad.iloc[:-1]
    elif kind == "reordered":
        bad = bad.iloc[::-1]
    elif kind == "clock":
        bad.loc[bad.index[0], "close"] = bad.open.iloc[0]
    elif kind == "break":
        bad.loc[bad.index[0], "break_start"] = bad.open.iloc[0]
    elif kind.startswith("naive_"):
        column = kind.removeprefix("naive_")
        bad[column] = bad[column].dt.tz_localize(None)
    else:
        bad.loc[bad.index[0], "close"] += pd.Timedelta(days=1)
    with pytest.raises(EquityAdmissionError):
        EquitySchedule(contract, bad)


@pytest.mark.parametrize("mode", [RunMode.LIVE, RunMode.DRY_RUN, RunMode.HYPEROPT])
def test_no_nonresearch_runtime(tmp_path, mode):
    dp, _, _, _, _ = setup_provider(tmp_path)
    with pytest.raises(EquityAdmissionError, match="research only"):
        EquityDataProvider(dict(dp._config, runmode=mode), dp.schedules)
