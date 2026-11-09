# ruff: noqa: S101
"""Forward snapshot boundaries and unmodified native position lifecycle."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from deploy.nfi80_daily.strategy import NFIDailyPaper as strategy
from freqtrade.freqtradebot import FreqtradeBot
from freqtrade.plugins.pairlist.RemotePairList import RemotePairList


def document(now):
    return {
        "pairs": [f"P{i}/USDT:USDT" for i in range(80)],
        "generated_at_utc": (now - timedelta(minutes=1)).isoformat(),
        "valid_until_utc": (now + timedelta(hours=23)).isoformat(),
        "native_cli_returncode": 0,
        "policy_sha256": "test-policy",
        "refresh_period": 60,
    }


@pytest.mark.parametrize(
    "change", ["expired", "future", "duplicate", "wrong_policy", "failed_native", "missing"]
)
def test_invalid_snapshots_block_new_entry_eligibility(tmp_path, change):
    now = datetime.now(UTC)
    value = document(now)
    path = tmp_path / "pool.json"
    if change == "expired":
        value["valid_until_utc"] = (now - timedelta(seconds=1)).isoformat()
    elif change == "future":
        value["generated_at_utc"] = (now + timedelta(minutes=1)).isoformat()
    elif change == "duplicate":
        value["pairs"][-1] = value["pairs"][0]
    elif change == "wrong_policy":
        value["policy_sha256"] = "other"
    elif change == "failed_native":
        value["native_cli_returncode"] = 1
    if change != "missing":
        path.write_text(json.dumps(value))
    assert strategy.snapshot_pairs(path, now, "test-policy") == ()


def test_fresh_snapshot_delegates_original_confirmation_and_excludes_removed_pair(
    tmp_path, monkeypatch
):
    now = datetime.now(UTC)
    path = tmp_path / "pool.json"
    path.write_text(json.dumps(document(now)))
    instance = object.__new__(strategy.NFIDailyFlatBoost)
    instance.daily_snapshot_path = path
    instance.daily_policy_sha256 = "test-policy"
    calls = []

    def confirm(self, *args, **kwargs):
        calls.append(args)
        return "native-result"

    monkeypatch.setattr(strategy.NFIFlatTopBoostPaper, "confirm_trade_entry", confirm)
    args = ("limit", 1, 2, "GTC", now, "141", "long")
    assert instance.confirm_trade_entry("P0/USDT:USDT", *args) == "native-result"
    assert instance.confirm_trade_entry("REMOVED/USDT:USDT", *args) is False
    path.write_text("invalid-json")
    assert instance.confirm_trade_entry("P0/USDT:USDT", *args) is False
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["live", "backtest"])
def test_daily_experiment_cannot_claim_historical_or_live_execution(mode):
    instance = object.__new__(strategy.NFIDailyFlatBoost)
    instance.config = {"runmode": SimpleNamespace(value=mode), "dry_run": True}
    with pytest.raises(ValueError, match="forward paper-only"):
        instance.bot_start()


def test_all_financial_functions_are_identical_to_validated_parent():
    for child, parent in [
        (strategy.NFIDailyFlatBoost, strategy.NFIFlatTopBoostPaper),
        (strategy.NFIDailyControl, strategy.NFIX8PaperControl),
    ]:
        for name in ("custom_stake_amount", "adjust_trade_position", "custom_exit", "leverage"):
            assert getattr(child, name) is getattr(parent, name)


def test_native_remote_file_loader_uses_the_shared_snapshot(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    path = tmp_path / "pool.json"
    value = document(now)
    path.write_text(json.dumps(value))
    exchange = SimpleNamespace(get_markets=lambda: dict.fromkeys(value["pairs"]))
    remote = RemotePairList(
        exchange,
        SimpleNamespace(),
        {},
        {
            "pairlist_url": "file:///" + str(path),
            "number_assets": 80,
            "refresh_period": 60,
        },
        0,
    )
    monkeypatch.setattr(remote, "_whitelist_for_active_markets", lambda pairs: pairs)
    assert remote.gen_pairlist({}) == value["pairs"]


def test_native_refresh_keeps_removed_open_position_for_management():
    new = ["NEW/USDT:USDT"]
    pairlists = SimpleNamespace(whitelist=["OLD/USDT:USDT"])

    def refresh():
        pairlists.whitelist = new.copy()

    pairlists.refresh_pairlist = refresh
    bot = SimpleNamespace(pairlists=pairlists, rpc=SimpleNamespace(send_msg=lambda message: None))
    active = FreqtradeBot._refresh_active_whitelist(bot, [SimpleNamespace(pair="OLD/USDT:USDT")])
    assert active == ["NEW/USDT:USDT", "OLD/USDT:USDT"]
