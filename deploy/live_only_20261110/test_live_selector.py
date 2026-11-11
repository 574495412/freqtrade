"""Live-only publisher boundary and notification routing, offline."""

# ruff: noqa: S101, RUF001 -- Pytest and Chinese fixtures.

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from deploy.mainnet_100_20261109 import live_selector as selector


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    snapshot = tmp_path / "pairs.json"
    folder = tmp_path / "futures"
    folder.mkdir()
    config = {
        "dry_run": False,
        "trading_mode": "futures",
        "pairlists": [
            {"method": "RemotePairList", "number_assets": 80, "pairlist_url": snapshot.as_uri()}
        ],
        "webhook": {"url": "http://127.0.0.1:18789/events/offline"},
    }
    path = folder / "config.json"
    path.write_text(json.dumps(config))
    data = {
        "snapshot_id": "old",
        "pairs": [f"COIN{i}/USDT:USDT" for i in range(80)],
        "valid_until_utc": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
    }
    snapshot.write_text(json.dumps(data))
    monkeypatch.setattr(selector, "STATE", tmp_path)
    monkeypatch.setattr(selector, "SNAPSHOT", snapshot)
    return path, snapshot, data


def test_valid_unchanged_snapshot_does_not_send_noise_or_create_ledger(prepared, tmp_path):
    calls = []
    result = selector.run(
        refresh=lambda: calls.append("refresh"),
        send=lambda *args: pytest.fail("No repeated update notice"),
    )
    assert calls == ["refresh"] and result["updated"] is False
    assert not list(tmp_path.rglob("*.sqlite"))


def test_changed_snapshot_uses_live_notification_and_same_native_input(prepared):
    _, path, data = prepared
    messages = []

    def refresh():
        path.write_text(json.dumps({**data, "snapshot_id": "new"}))

    result = selector.run(refresh=refresh, send=lambda url, body: messages.append(body))
    assert result["updated"] and "永续实盘" in messages[0] and "模拟" not in messages[0]


@pytest.mark.parametrize("change", ["paper", "wrong_snapshot", "remote_notification"])
def test_wrong_context_rejected_before_refresh(prepared, change):
    path, _, _ = prepared
    config = json.loads(path.read_text())
    if change == "paper":
        config["dry_run"] = True
    elif change == "wrong_snapshot":
        config["pairlists"][0]["pairlist_url"] = "file:///wrong"
    else:
        config["webhook"]["url"] = "https://example.com/private"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        selector.run(refresh=lambda: pytest.fail("Context must be checked first"))


def test_failed_refresh_notifies_without_private_exception_text(prepared):
    notices = []

    def fail():
        raise ValueError("private signature or URL")

    with pytest.raises(ValueError):
        selector.run(refresh=fail, send=lambda url, body: notices.append(body))
    assert notices == ["[NFI X8｜永续实盘] 日更选币数据更新失败：ValueError"]


def test_expired_snapshot_not_reported_as_success(prepared):
    _, path, data = prepared
    path.write_text(json.dumps({**data, "valid_until_utc": "2000-01-01T00:00:00+00:00"}))
    notices = []
    with pytest.raises(ValueError):
        selector.run(refresh=lambda: None, send=lambda url, body: notices.append(body))
    assert len(notices) == 1 and "失败" in notices[0]


def test_same_completed_day_skips_heavy_refresh_after_source_check(prepared, monkeypatch):
    _, snapshot, data = prepared
    checks = []
    monkeypatch.setattr(selector, "verify_cached_source", lambda: checks.append("verified"))
    data.update(
        policy_sha256=hashlib.sha256(selector.POLICY.read_bytes()).hexdigest(),
        decision_day_utc=datetime.now(UTC).date().isoformat(),
    )
    snapshot.write_text(json.dumps(data))
    result = selector.run(send=lambda *args: pytest.fail("No repeated notice"))
    assert checks == ["verified"]
    assert result["cached_inputs"] and result["updated"] is False


@pytest.mark.parametrize("change", ["policy", "day", "expired", "count"])
def test_cached_fast_path_rejects_invalid_or_old_snapshot(prepared, change):
    _, _, data = prepared
    data.update(
        policy_sha256=hashlib.sha256(selector.POLICY.read_bytes()).hexdigest(),
        decision_day_utc=datetime.now(UTC).date().isoformat(),
    )
    if change == "policy":
        data["policy_sha256"] = "changed"
    elif change == "day":
        data["decision_day_utc"] = "2000-01-01"
    elif change == "expired":
        data["valid_until_utc"] = "2000-01-01T00:00:00+00:00"
    else:
        data["pairs"].pop()
    assert not selector.completed_today(data)


def test_new_day_still_uses_full_manual_gate_and_existing_publisher(prepared, monkeypatch):
    from deploy.mainnet_100_20261109 import manual
    from deploy.nfi80_selected import selector as native_selector

    _, path, data = prepared
    calls = []
    monkeypatch.setattr(selector, "verify_cached_source", lambda: calls.append("source"))
    monkeypatch.setattr(manual, "verify", lambda: calls.append("full_manual_gate"))

    def refresh():
        calls.append("native_publisher")
        path.write_text(json.dumps({**data, "snapshot_id": "new"}))

    monkeypatch.setattr(native_selector, "main", refresh)
    result = selector.run(send=lambda *args: calls.append("notify"))
    assert result["updated"]
    assert calls == ["source", "full_manual_gate", "native_publisher", "notify"]


def test_cached_selector_rejects_changed_private_config(prepared, tmp_path, monkeypatch):
    path, _, _ = prepared
    path.chmod(0o600)
    (tmp_path / "release.json").write_text(json.dumps({"config_sha256": {"futures": "wrong"}}))
    (tmp_path / "audit-hold.json").write_text(json.dumps({"status": "RELEASED_BY_USER"}))
    monkeypatch.setattr(selector, "HERE", tmp_path)
    with pytest.raises(ValueError, match="configuration or source"):
        selector.verify_cached_source()
