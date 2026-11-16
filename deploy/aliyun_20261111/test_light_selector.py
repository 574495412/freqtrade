"""Parity against the pinned RuleBook, CCXT/native market gates and cold imports."""
# ruff: noqa: S101

import copy
import importlib.util
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import ccxt
import pandas as pd
import pytest

from deploy.nfi80_selected import light_selector as light
from deploy.nfi80_selected import selector
from freqtrade.enums import TradingMode
from freqtrade.exchange.binance import Binance


def reference_module():
    policy = selector.read(selector.HERE / "policy.json")
    spec = importlib.util.spec_from_file_location(
        "original_structural_rulebook", policy["rulebook"]["path"]
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candles(day, count, quote=1.0):
    end = int(day.timestamp() * 1000)
    return [
        [
            end - n * light.DAY_MS,
            "1",
            "2",
            "0.5",
            "1.5",
            "5",
            end - (n - 1) * light.DAY_MS - 1,
            str(quote),
        ]
        for n in range(count, 0, -1)
    ]


def as_frame(raw):
    return pd.DataFrame(
        {
            "date": pd.to_datetime([v[0] for v in raw], unit="ms", utc=True),
            "close_time": pd.to_datetime([v[6] for v in raw], unit="ms", utc=True),
            **{
                name: [float(v[i]) for v in raw]
                for name, i in [
                    ("open", 1),
                    ("high", 2),
                    ("low", 3),
                    ("close", 4),
                    ("volume", 5),
                    ("quote_volume", 7),
                ]
            },
        }
    )


@pytest.mark.parametrize("offset", [0, 1, 30])
def test_structural_ranking_matches_unmodified_pinned_rulebook(tmp_path, offset):
    day = datetime(2026, 11, 16, tzinfo=UTC) + timedelta(days=offset)
    raw = {
        f"COIN{i}/USDT:USDT": candles(day, 59 if i % 17 == 0 else 65, -1 if i % 19 == 0 else i // 3)
        for i in range(125)
    }
    blacklist = ["coin1[01]/USDT:USDT", "COIN12[0-4]/.*"]
    records = {}
    for pair, rows in raw.items():
        path = tmp_path / (pair.split("/")[0] + ".parquet")
        as_frame(rows).to_parquet(path)
        records[pair] = {"path": str(path), "sha256": selector.sha(path)}
    book = reference_module().RuleBook(
        {"variant": "structural", "daily_inputs": records, "structural_blacklist": blacklist},
        None,
        None,
        {},
    )
    expected = book.select(pd.Timestamp(day))
    actual = light.structural_selection(
        {p: light.daily_summary(r, 0, int(day.timestamp() * 1000)) for p, r in raw.items()},
        blacklist,
        int(day.timestamp() * 1000),
    )
    assert actual == expected


@pytest.mark.parametrize("change", ["missing_day", "duplicate", "unsorted", "nonfinite", "empty"])
def test_daily_input_faults_cannot_silently_rerank(change):
    day = datetime(2026, 11, 16, tzinfo=UTC)
    raw = candles(day, 65)
    if change == "missing_day":
        raw.pop()
    elif change == "duplicate":
        raw[-2] = raw[-1]
    elif change == "unsorted":
        raw[-2], raw[-3] = raw[-3], raw[-2]
    elif change == "nonfinite":
        raw[-1][7] = "NaN"
    else:
        raw = []
    with pytest.raises(ValueError):
        light.daily_summary(raw, 0, int(day.timestamp() * 1000))


def test_onboard_filter_matches_reference_and_drops_open_candle():
    day = datetime(2026, 11, 16, tzinfo=UTC)
    ms = int(day.timestamp() * 1000)
    raw = candles(day + timedelta(days=1), 66)
    assert light.daily_summary(raw, ms - 60 * light.DAY_MS + 12345, ms)["count"] == 60


def test_market_projection_matches_native_ccxt_filters_on_frozen_contracts():
    policy = selector.read(selector.HERE / "policy.json")
    identities = selector.read(policy["market_identity_source"]["path"])["markets"]
    raws = [copy.deepcopy(identities[p]["info"]) for p in policy["candidates"]]
    # Exercise active/inactive, futures vs swap, tiny precision and linearity.
    raws[0]["status"] = "SETTLING"
    raws[1]["contractType"] = "CURRENT_QUARTER"
    raws[1]["deliveryDate"] = 1800000000000
    raws[2]["marginAsset"] = raws[2]["baseAsset"]
    for f in raws[3]["filters"]:
        if f["filterType"] == "PRICE_FILTER":
            f["tickSize"] = "0.000000000001"
    api = ccxt.binance()
    markets = {m["symbol"]: m for m in [api.parse_market(raw) for raw in raws]}
    exchange = object.__new__(Binance)
    exchange._api = api
    exchange._markets = markets
    exchange.trading_mode = TradingMode.FUTURES
    exchange._ft_has = {"ccxt_futures_name": "swap"}
    exchange.close = lambda: None
    expected = exchange.get_markets(quote_currencies=["USDT"], futures_only=True, active_only=True)
    projected = light.eligible_markets({"symbols": raws}, policy["candidates"], identities)
    assert list(projected) == [p for p in policy["candidates"] if p in expected]
    for pair in projected:
        assert expected[pair]["id"] == projected[pair]["symbol"]


def test_changed_contract_identity_is_rejected():
    policy = selector.read(selector.HERE / "policy.json")
    identities = selector.read(policy["market_identity_source"]["path"])["markets"]
    pair = policy["candidates"][5]
    raw = copy.deepcopy(identities[pair]["info"])
    raw["status"] = "TRADING"
    raw["onboardDate"] += 1
    with pytest.raises(ValueError, match="identity changed"):
        light.eligible_markets({"symbols": [raw]}, [pair], identities)


def test_full_live_admission_and_light_backend_import_no_trading_stack():
    code = """
import sys
from deploy.mainnet_100_20261109.manual import verify
from deploy.nfi80_selected.light_selector import main
assert not any(n in sys.modules for n in ['pandas','numpy','ccxt','sqlalchemy'])
"""
    subprocess.run([sys.executable, "-c", code], check=True, cwd=selector.ROOT)


def test_injected_clock_cannot_publish_live_snapshot(monkeypatch):
    from deploy.mainnet_100_20261109 import manual

    monkeypatch.setattr(manual, "verify", lambda: None)
    with pytest.raises(ValueError, match="nonpublishing"):
        light.main(now=datetime(2026, 11, 16, tzinfo=UTC))


def test_shipped_projection_matches_all_original_pinned_inputs():
    from deploy.nfi80_selected.build_input_projection import build

    assert selector.read(selector.HERE / "input-projection.json") == build()


def test_failed_attempt_retains_closed_inputs_and_never_replaces_pool(tmp_path, monkeypatch):  # noqa: C901 -- HTTP outage and UTC rollover integration fixture.
    from deploy.mainnet_100_20261109 import manual

    policy = selector.read(selector.HERE / "policy.json")
    identities = selector.read(policy["market_identity_source"]["path"])["markets"]
    raws = [copy.deepcopy(identities[p]["info"]) for p in policy["candidates"]]
    for raw in raws:
        raw["status"] = "TRADING"
    day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    calls, fail = [], {"symbol": raws[10]["symbol"]}

    class Response:
        def __init__(self, value):
            self.value = value

        def raise_for_status(self):
            pass

        def json(self):
            return self.value

    class Session:
        def __init__(self, **kwargs):
            # This unsigned public reader must tolerate >10s contention without
            # using an unbounded wait or changing the shared request policy.
            assert 10 < kwargs["max_wait"] <= 135

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, *, params, **kwargs):
            assert url.startswith("https://fapi.binance.com/fapi/v1/")
            if url.endswith("exchangeInfo"):
                return Response({"symbols": raws})
            assert params["endTime"] == int(day.timestamp() * 1000) - 1
            symbol = params["symbol"]
            calls.append(symbol)
            if symbol == fail["symbol"]:
                raise TimeoutError("test input outage")
            return Response(candles(day, 65, 1000.0))

    monkeypatch.setattr(manual, "verify", lambda: None)
    monkeypatch.setattr(light, "GuardedSession", Session)
    monkeypatch.setattr(selector, "STATE", tmp_path)
    monkeypatch.setattr(selector, "SNAPSHOT", tmp_path / "current.json")
    monkeypatch.setattr(selector, "runtime_context", lambda: {"https_proxy": None})
    selector.SNAPSHOT.write_text('{"snapshot_id":"previous"}')
    with pytest.raises(TimeoutError):
        light.main(force_refresh=True)
    assert selector.read(selector.SNAPSHOT) == {"snapshot_id": "previous"}
    previous_calls = len(calls)
    assert len(list((tmp_path / "closed-daily-cache").rglob("*.json"))) > 5
    fail["symbol"] = None
    result = light.main(force_refresh=True, publish=False)
    assert result["published"] is False
    assert len(calls) - previous_calls < len(raws) - 5
    assert selector.read(selector.SNAPSHOT) == {"snapshot_id": "previous"}
    snapshot = selector.read(Path(result["attempt"]) / "selected-snapshot.json")
    assert datetime.fromisoformat(snapshot["valid_until_utc"]) == day + timedelta(days=1)
    assert snapshot["decision_day_utc"] == day.date().isoformat()
    replay = light.main(force_refresh=True, publish=False, now=day)
    assert replay["attempt"] != result["attempt"]
    assert selector.read(selector.SNAPSHOT) == {"snapshot_id": "previous"}

    # A job crossing UTC midnight must retry the new day, never stamp old inputs
    # with a fresh validity window or replace the active consumer snapshot.
    advanced = {"value": False}

    class MovingClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return day + timedelta(days=int(advanced["value"]))

    get = Session.get

    def advance_after_metadata(self, url, **kwargs):
        response = get(self, url, **kwargs)
        if url.endswith("exchangeInfo"):
            advanced["value"] = True
        return response

    monkeypatch.setattr(light, "datetime", MovingClock)
    monkeypatch.setattr(Session, "get", advance_after_metadata)
    with pytest.raises(ValueError, match="UTC day changed"):
        light.main(force_refresh=True)
    assert selector.read(selector.SNAPSHOT) == {"snapshot_id": "previous"}
