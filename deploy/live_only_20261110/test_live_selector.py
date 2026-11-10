"""Live-only publisher boundary and notification routing, offline."""

# ruff: noqa: S101, RUF001 -- Pytest and Chinese fixtures.

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
