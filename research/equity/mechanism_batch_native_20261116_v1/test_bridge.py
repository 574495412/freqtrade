from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from bootstrap import boot


boot()
from RegistrySignal import RegistryFeatures, RegistrySignal
from src.factors.registry import Registry

from freqtrade.data.equity_provider import EquitySchedule
from freqtrade.data.history.equity import EquityAdmissionError, EquitySessionContract


def provider():
    dates = pd.date_range("2024-01-01", periods=300, freq="B", tz="UTC")
    calendar = pd.DataFrame(index=dates)
    calendar["open"] = dates + pd.Timedelta(hours=14)
    calendar["close"] = dates + pd.Timedelta(hours=21)
    calendar["break_start"] = pd.NaT
    calendar["break_end"] = pd.NaT
    pairs = ["A/USD", "B/USD", "C/USD"]
    frames = {}
    schedules = {}
    for i, pair in enumerate(pairs):
        p = 100 + i + np.arange(len(dates)) * 0.1 + np.sin(np.arange(len(dates)) * (i + 1) / 10)
        frames[pair] = pd.DataFrame(
            {
                "date": dates,
                "open": p,
                "high": p + 1,
                "low": p - 1,
                "close": p,
                "volume": 100.0 + np.arange(len(dates)),
            }
        )
        schedules[pair] = EquitySchedule(
            EquitySessionContract(pair, "XNYS", tuple(dates.strftime("%Y-%m-%d"))), calendar
        )
    return (
        SimpleNamespace(
            schedules=schedules,
            _equity_asof=None,
            historic_ohlcv=lambda pair, timeframe: frames[pair].copy(),
        ),
        frames,
        pairs,
    )


def test_callback_requires_reached_completed_close():
    dp, _frames, pairs = provider()
    bridge = RegistryFeatures(Registry(), dp, pairs, ["academic_illiq"], "equity_us")
    with pytest.raises(EquityAdmissionError, match="clock"):
        bridge.visible("academic_illiq", pairs[0])
    close = dp.schedules[pairs[0]].frame.close.iloc[100]
    dp._equity_asof = close - pd.Timedelta(nanoseconds=1)
    assert len(bridge.visible("academic_illiq", pairs[0])) == 100
    dp._equity_asof = close
    assert len(bridge.visible("academic_illiq", pairs[0])) == 101
    dp._equity_asof = close.tz_localize(None)
    with pytest.raises(EquityAdmissionError, match="Timezone-aware"):
        bridge.visible("academic_illiq", pairs[0])


def test_original_market_metadata_not_silently_extended():
    dp, _, pairs = provider()
    with pytest.raises(EquityAdmissionError, match="metadata"):
        RegistryFeatures(Registry(), dp, pairs, ["alpha101_006"], "equity_hk")


def test_attach_rejects_changed_source_and_pair():
    dp, frames, pairs = provider()
    bridge = RegistryFeatures(Registry(), dp, pairs, ["academic_illiq"], "equity_us")
    frame = frames[pairs[0]].copy()
    frame.loc[200, "volume"] *= 2
    with pytest.raises(EquityAdmissionError, match="source"):
        bridge.attach(frame, pairs[0])
    with pytest.raises(EquityAdmissionError, match="exact pair"):
        bridge.attach(frames[pairs[0]], "OTHER/USD")
    with pytest.raises(EquityAdmissionError, match="exact pair"):
        bridge.attach(frames[pairs[0]].iloc[1:], pairs[0])


def test_duplicate_and_cross_market_peers_reject():
    dp, _, pairs = provider()
    with pytest.raises(EquityAdmissionError, match="Unique"):
        RegistryFeatures(Registry(), dp, [pairs[0], pairs[0]], ["academic_illiq"], "equity_us")
    dp.schedules[pairs[1]] = SimpleNamespace(contract=SimpleNamespace(calendar="XHKG"))
    with pytest.raises(EquityAdmissionError, match="Cross-market"):
        RegistryFeatures(Registry(), dp, pairs, ["academic_illiq"], "equity_us")


def test_owning_registry_scores_attach_exactly_and_unknowns_stay_unknown():
    dp, frames, pairs = provider()
    bridge = RegistryFeatures(
        Registry(), dp, pairs, ["academic_bab", "academic_illiq", "alpha101_006"], "equity_us"
    )
    advised = bridge.attach(frames[pairs[0]], pairs[0])
    assert advised.academic_bab.iloc[:252].isna().all()
    assert advised.academic_illiq.iloc[:21].isna().all()
    assert advised.alpha101_006.iloc[:9].isna().all()
    for identity in bridge.factor_ids:
        np.testing.assert_array_equal(advised[identity], bridge.scores[identity][pairs[0]])


def test_no_funded_loop_or_order_authorization():
    with pytest.raises(EquityAdmissionError, match="closed"):
        RegistrySignal({}).bot_start()
    strategy = RegistrySignal({"equity_diagnostic_only": True})
    strategy.bot_start()
    with pytest.raises(EquityAdmissionError, match="funded loop"):
        strategy.bot_loop_start()
    assert not strategy.confirm_trade_entry()
