"""Real receive-time causality, normal-flow semantics and loss-visible capture."""

import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from freqtrade.research.data import read
from research.crypto.microstructure_capture import RawWriter
from research.crypto.microstructure_features import FeatureBuilder, normal_trade, visible_book


def plan():
    result = read(Path("research/crypto/microstructure_plan_v001.json"))
    result["symbols"] = ["BTCUSDT"]
    return result


def book(update=1):
    return {
        "e": "depthUpdate",
        "st": 1,
        "s": "BTCUSDT",
        "E": 1000,
        "T": 999,
        "u": update,
        "pu": update - 1,
        "b": [[str(100 - 0.01 * i), "2"] for i in range(20)],
        "a": [[str(100.1 + 0.01 * i), "1"] for i in range(20)],
    }


def trade(identifier=1):
    return {
        "e": "aggTrade",
        "st": 1,
        "s": "BTCUSDT",
        "E": 1000,
        "T": 999,
        "a": identifier,
        "p": "100",
        "q": "4",
        "nq": "2",
        "m": False,
        "f": identifier,
        "l": identifier,
    }


def records(seconds=180):
    result = []
    for second in range(seconds):
        for offset, channel, data in [
            (1, "depth", book(second + 1)),
            (2, "trades", trade(second + 1)),
        ]:
            now = second * 1_000_000_000 + offset * 1_000_000
            stream = "btcusdt@depth20@100ms" if channel == "depth" else "btcusdt@aggTrade"
            result.append(
                {
                    "sequence": len(result),
                    "received_monotonic_ns": now,
                    "received_wall_ns": now + 1_700_000_000_000_000_000,
                    "channel": channel,
                    "epoch": 0,
                    "raw": json.dumps({"stream": stream, "data": data}),
                }
            )
    return result


def build(raw):
    builder = FeatureBuilder(plan())
    for record in raw:
        builder.ingest(record)
    return builder, pd.DataFrame(builder.finish(raw[-1]["received_monotonic_ns"]))


def test_book_formula_uses_opposite_price_weight_and_visible_notional():
    result = visible_book(book())
    assert result["imbalance_l1"] == pytest.approx(1 / 3)
    assert result["microprice_deviation_bps"] > 0
    assert result["bid"] < result["mid"] < result["ask"]
    assert -1 <= result["imbalance_l20"] <= 1


@pytest.mark.parametrize("fault", ["crossed", "zero", "unordered", "wrong_type"])
def test_invalid_books_do_not_become_features(fault):
    data = book()
    if fault == "crossed":
        data["a"][0][0] = "99"
    elif fault == "zero":
        data["b"][0][1] = "0"
    elif fault == "unordered":
        data["b"][1][0] = "101"
    else:
        data["st"] = 2
    with pytest.raises(ValueError):
        visible_book(data)


def test_rpi_and_aggressor_fields_are_not_silently_replaced():
    parsed = normal_trade(trade())
    assert parsed["normal"] == 2 and parsed["quantity"] == 4 and parsed["sign"] == 1
    sell = trade()
    sell["m"] = True
    assert normal_trade(sell)["sign"] == -1
    del sell["nq"]
    with pytest.raises(KeyError):
        normal_trade(sell)


def test_future_and_backdated_exchange_messages_cannot_change_past_features():
    raw = records()
    _, original = build(raw)
    changed = [dict(record) for record in raw]
    for record in changed[260:]:
        payload = json.loads(record["raw"])
        payload["data"]["E"] = payload["data"]["T"] = 1
        if record["channel"] == "trades":
            payload["data"]["m"] = True
        else:
            payload["data"]["b"][0][1] = "2000"
        record["raw"] = json.dumps(payload)
    _, modified = build(changed)
    pd.testing.assert_frame_equal(original.iloc[:129], modified.iloc[:129])
    _, prefix = build(raw[:260])
    pd.testing.assert_frame_equal(prefix, original.iloc[: len(prefix)].reset_index(drop=True))


def test_trade_gaps_reset_observation_window_instead_of_filling_missing_flow():
    raw = records(260)
    for record in raw[260:]:
        if record["channel"] == "trades":
            payload = json.loads(record["raw"])
            payload["data"]["a"] += 10
            record["raw"] = json.dumps(payload)
    builder, result = build(raw)
    assert builder.counts["trade_id_gap:BTCUSDT"] == 1
    at = result.set_index(result.decision_monotonic_ns // 1_000_000_000)
    assert np.isfinite(at.loc[129, "received_flow_120s"])
    assert np.isnan(at.loc[140, "received_flow_30s"])
    assert np.isnan(at.loc[240, "received_flow_120s"])
    assert np.isfinite(at.loc[252, "received_flow_120s"])


def test_stale_book_and_disconnected_trade_epoch_are_unknown():
    raw = records()
    for record in raw[300:]:
        if record["channel"] == "trades":
            record["epoch"] = 1
    builder, result = build(raw)
    assert np.isnan(result.iloc[155].received_flow_120s)
    builder.finish(raw[-1]["received_monotonic_ns"] + 10_000_000_000)
    assert np.isnan(builder.rows[-1]["spread_bps"])
    assert not builder.rows[-1]["complete_factors"]


def test_wall_clock_step_remains_visible_and_invalidates_completion():
    raw = records()
    for record in raw[260:]:
        record["received_wall_ns"] += 1_000_000_000
    builder, result = build(raw)
    assert builder.counts["wall_clock_step_records"] > 0
    assert not result.iloc[-1].wall_clock_valid
    assert not result.iloc[-1].complete_factors


def test_raw_writer_preserves_original_payload_and_enforces_byte_cap(tmp_path):
    writer = RawWriter(tmp_path, 10_000, 60)
    original = {"raw": '{"q":"000.100","nq":"000.050"}', "sequence": 0}
    writer.append(original)
    writer.close()
    with gzip.open(tmp_path / writer.closed[0]["file"], "rt") as stream:
        assert json.loads(stream.read()) == original
    capped = RawWriter(tmp_path, 10, 60)
    with pytest.raises(OSError, match="byte cap"):
        capped.append(original)
    assert capped.rows == 0
