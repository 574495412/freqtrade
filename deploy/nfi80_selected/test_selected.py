# ruff: noqa: S101
"""Operational gates must fail closed without changing validated NFI finances."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from deploy.nfi80_selected.strategy import NFISelectedPaper as strategy


def document(now):
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "pairs": [f"P{i}/USDT:USDT" for i in range(80)],
        "generated_at_utc": now.isoformat(),
        "valid_until_utc": (day + timedelta(days=1)).isoformat(),
        "decision_day_utc": day.date().isoformat(),
        "latest_input_close": (day - timedelta(milliseconds=1)).isoformat(),
        "selection_returncode": 0,
        "policy_sha256": "policy",
    }


@pytest.mark.parametrize("bad", ["expired", "future_input", "duplicate", "wrong_policy"])
def test_invalid_data_blocks_initial_entries(tmp_path, bad):
    now = datetime(2026, 11, 9, 14, tzinfo=UTC)
    value = document(now)
    if bad == "expired":
        now += timedelta(days=1)
    elif bad == "future_input":
        value["latest_input_close"] = now.isoformat()
    elif bad == "duplicate":
        value["pairs"][1] = value["pairs"][0]
    else:
        value["policy_sha256"] = "other"
    path = tmp_path / "pool.json"
    path.write_text(json.dumps(value))
    assert not strategy.snapshot_pairs(path, now, "policy")


def test_actual_pool_must_match_before_delegating(tmp_path, monkeypatch):
    now = datetime(2026, 11, 9, 14, tzinfo=UTC)
    value = document(now)
    path = tmp_path / "pool.json"
    path.write_text(json.dumps(value))
    instance = object.__new__(strategy.NFISelectedFlatBoost)
    instance.daily_snapshot_path, instance.daily_policy_sha256 = path, "policy"
    actual = value["pairs"].copy()
    instance.dp = SimpleNamespace(current_whitelist=lambda: actual)
    calls = []
    monkeypatch.setattr(
        strategy.NFIFlatTopBoostPaper,
        "confirm_trade_entry",
        lambda *a, **k: calls.append(a) or True,
    )
    args = ("limit", 1, 2, "GTC", now, "141", "long")
    assert instance.confirm_trade_entry(value["pairs"][0], *args)
    actual.pop()
    assert not instance.confirm_trade_entry(value["pairs"][0], *args)
    assert len(calls) == 1


def test_financial_callbacks_remain_inherited():
    for name in ("custom_stake_amount", "adjust_trade_position", "custom_exit", "leverage"):
        assert getattr(strategy.NFISelectedFlatBoost, name) is getattr(
            strategy.NFIFlatTopBoostPaper, name
        )
    instance = object.__new__(strategy.NFISelectedFlatBoost)
    instance.config = {"dry_run": False, "runmode": SimpleNamespace(value="live")}
    with pytest.raises(ValueError, match="paper-only"):
        instance.bot_start()
