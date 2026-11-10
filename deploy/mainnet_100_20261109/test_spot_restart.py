# ruff: noqa: S101
"""Maintenance scopes a graceful restart to one existing flat spot ledger."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from deploy.mainnet_100_20261109 import spot_restart as repair


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    state = tmp_path / "state"
    spot = state / "spot"
    spot.mkdir(parents=True)
    here = tmp_path / "source"
    here.mkdir()
    for name in ("prepare.py", "spot_transport.py", "spot_restart.py"):
        (here / name).write_text("public source fixture")
    (here / "release.json").write_text(json.dumps({"config_sha256": {"spot": "old"}, "files": {}}))
    (spot / "config.json").write_text(
        json.dumps({"capital_ceiling_usdt": 100, "available_capital": 99})
    )
    (spot / "worker.pid").write_text(json.dumps({"pid": 12}))
    (spot / "nfix8-profit_max-test.json").write_text('{"existing":"retained"}')
    with sqlite3.connect(spot / "trades.sqlite") as c:
        c.execute("create table trades(id integer)")
        c.execute("create table orders(id integer)")
    folder = state / "maintenance"
    folder.mkdir()
    candidate = {"capital_ceiling_usdt": 100, "available_capital": 99}
    monkeypatch.setattr(repair, "STATE", state)
    monkeypatch.setattr(repair, "ROOT", tmp_path)
    monkeypatch.setattr(repair, "HERE", here)
    monkeypatch.setattr(repair, "prepare", lambda: (folder, candidate))
    return spot, folder, tmp_path


def test_active_ledger_refuses_before_any_signal(prepared, monkeypatch):
    spot, _, _ = prepared
    with sqlite3.connect(spot / "trades.sqlite") as c:
        c.execute("insert into trades values(1)")
    monkeypatch.setattr(repair.psutil, "Process", lambda pid: pytest.fail("PID lookup reached"))
    with pytest.raises(ValueError, match="flat ledger"):
        repair.apply()


def test_futures_pid_cannot_be_signaled(prepared, monkeypatch):
    _, _, root = prepared
    old = SimpleNamespace(
        cmdline=lambda: ["python", "--worker", "futures"],
        cwd=lambda: str(root),
        terminate=lambda: pytest.fail("Wrong PID signaled"),
    )
    monkeypatch.setattr(repair.psutil, "Process", lambda pid: old)
    with pytest.raises(ValueError, match="identity"):
        repair.apply()


def test_flat_restart_retains_ledger_cache_and_selects_only_spot(prepared, monkeypatch):
    spot, folder, root = prepared
    called = []
    old = SimpleNamespace(
        pid=12,
        cmdline=lambda: ["python", "--worker", "spot"],
        cwd=lambda: str(root),
        terminate=lambda: called.append("terminate-spot"),
        wait=lambda timeout: called.append(("wait", timeout)),
    )
    monkeypatch.setattr(repair.psutil, "Process", lambda pid: old)
    from deploy.mainnet_100_20261109 import manual, spot_status

    monkeypatch.setattr(manual, "verify", lambda: called.append("verify"))
    monkeypatch.setattr(spot_status, "observe", lambda: {"flat_state_snapshot_reconciled": True})

    def spawn(args, **kwargs):
        assert args[-2:] == ["--worker", "spot"]
        called.append("restart-spot")
        return SimpleNamespace(pid=99)

    monkeypatch.setattr(repair.subprocess, "Popen", spawn)
    repair.apply()
    assert called == ["terminate-spot", ("wait", 45), "verify", "restart-spot"]
    assert repair.ledger_counts(spot / "trades.sqlite") == {"trades": 0, "orders": 0}
    assert repair.ledger_counts(folder / "trades-before.sqlite") == {"trades": 0, "orders": 0}
    assert (spot / "nfix8-profit_max-test.json").read_bytes() == (
        folder / "nfix8-profit_max-test.json"
    ).read_bytes()
    assert json.loads((spot / "worker.pid").read_text())["pid"] == 99
