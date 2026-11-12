"""Linux preparation preserves the live financial and single-worker contracts."""
# ruff: noqa: S101

import sqlite3
import subprocess
from pathlib import Path

import pytest

from deploy.aliyun_20261111.prepare import linux_config, unit


def test_linux_relocation_preserves_financial_contract_and_input():
    config = {
        "dry_run": False,
        "initial_state": "stopped",
        "available_capital": 99,
        "capital_ceiling_usdt": 100,
        "strategy": "NFIManualFlatBoost",
        "margin_mode": "isolated",
        "nfi_parameters": {"leverage": 3},
        "max_open_trades": 12,
        "daily_snapshot_file": "/old/user_data/pairs.json",
        "pairlists": [{"pairlist_url": "file:///old/user_data/pairs.json"}],
        "exchange": {
            "ccxt_config": {"httpsProxy": "http://127.0.0.1:7897"},
            "ccxt_async_config": {"httpsProxy": "http://127.0.0.1:7897"},
        },
    }
    result = linux_config(config, Path("/old"), Path("/opt/freqtrade"))
    assert result["pairlists"][0]["pairlist_url"] == "file:///opt/freqtrade/user_data/pairs.json"
    assert result["daily_snapshot_file"] == "/opt/freqtrade/user_data/pairs.json"
    assert result["initial_state"] == "stopped"
    for key in (
        "available_capital",
        "capital_ceiling_usdt",
        "margin_mode",
        "nfi_parameters",
        "max_open_trades",
    ):
        assert result[key] == config[key]
    assert result["exchange"]["ccxt_config"] == {}
    assert config["exchange"]["ccxt_config"]["httpsProxy"]


@pytest.mark.parametrize(
    "change", [{"dry_run": True}, {"available_capital": 495}, {"initial_state": "running"}]
)
def test_other_account_contract_refuses_preparation(change):
    config = {
        "dry_run": False,
        "initial_state": "stopped",
        "available_capital": 99,
        "capital_ceiling_usdt": 100,
        "strategy": "NFIManualFlatBoost",
    }
    with pytest.raises(ValueError):
        linux_config({**config, **change})


def test_systemd_uses_original_locked_worker_and_holds_restarts():
    service = unit("futures", "deploy.mainnet_100_20261109.manual", "--worker futures")
    assert "-m deploy.mainnet_100_20261109.manual --worker futures" in service
    assert "User=freqtrade" in service and "Restart=no" in service
    assert "ConditionPathExists=/run/freqtrade-live-admitted" in service
    assert "Slice=freqtrade-live.slice" in service
    assert "UMask=0077" in service
    assert "ExecStartPost=" not in service


@pytest.mark.parametrize("name", ["spot", "futures", "notifications", "selector"])
def test_ecs_workload_is_bounded_and_restart_storms_stop(name):
    service = unit(name, "example.module")
    assert "MemoryMax=" in service and "MemorySwapMax=" in service
    assert "CPUQuota=" in service and "TasksMax=64" in service
    assert "StartLimitIntervalSec=900" in service and "StartLimitBurst=3" in service
    assert "RestartSec=180" in service and "OOMPolicy=stop" in service
    assert "Environment=OPENBLAS_NUM_THREADS=1" in service


def test_stopped_protection_preserves_budget_and_never_starts_worker(tmp_path, monkeypatch):
    from deploy.aliyun_20261111 import systemd

    state = tmp_path / "user_data/deployment"
    state.mkdir(parents=True)
    units = tmp_path / "units"
    units.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(systemd, "REMOTE_ROOT", tmp_path)
    monkeypatch.setattr(systemd, "SYSTEMD_DIRECTORY", units)
    monkeypatch.setattr(systemd, "ADMISSION", tmp_path / "admission")
    monkeypatch.setattr(systemd.os, "geteuid", lambda: 0)
    calls = []
    monkeypatch.setattr(systemd.subprocess, "run", lambda args, **kwargs: calls.append(args))
    monkeypatch.setattr(systemd.subprocess, "check_output", lambda *a, **k: "inactive\n")
    database = state / "binance-request-guard.sqlite"
    with sqlite3.connect(database) as conn:
        conn.executescript("""
            CREATE TABLE budget(cooldown REAL,strikes INTEGER);
            INSERT INTO budget VALUES(2000000000,7);
            CREATE TABLE leases(ticket TEXT,expires REAL);
            INSERT INTO leases VALUES('active',2000000000);
            CREATE TABLE public_cache(key TEXT,value TEXT,expires REAL,owner TEXT,lease_until REAL);
            INSERT INTO public_cache VALUES('keep','[]',2000000000,'active',2000000000);
        """)
    before = database.read_bytes()
    old = units / "freqtrade-live-spot.service"
    old.write_text("previous unit")
    systemd.apply()
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT * FROM budget").fetchall() == [(2000000000, 7)]
        assert conn.execute("SELECT * FROM leases").fetchall() == [('active', 2000000000)]
        assert conn.execute("SELECT * FROM public_cache").fetchall() == [
            ('keep', '[]', 2000000000, 'active', 2000000000)
        ]
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert database.read_bytes() != before  # An additive schema migration occurred.
    assert all("start" not in c and "restart" not in c and "stop" not in c for c in calls)
    assert any(c[:2] == ["systemd-analyze", "verify"] for c in calls)
    assert next(state.glob("resource-protection-*/*.before")).read_text() == "previous unit"
    assert "MemoryMax=900M" in (units / "freqtrade-live.slice").read_text()
    for name in systemd.SERVICES:
        service = (units / name).read_text()
        assert "Slice=freqtrade-live.slice" in service
        assert f"ConditionPathExists={systemd.ADMISSION}" in service
        assert "Restart=no" in service


@pytest.mark.parametrize("active", ["active", "activating", "deactivating", "reloading", ""])
def test_aggregate_install_refuses_live_or_unknown_unit_state(tmp_path, monkeypatch, active):
    from deploy.aliyun_20261111 import systemd

    monkeypatch.setattr(systemd.subprocess, "check_output", lambda *a, **k: active)
    monkeypatch.setattr(systemd, "ADMISSION", tmp_path / "admission")
    with pytest.raises(ValueError, match="Stop freqtrade-live-spot"):
        systemd.require_stopped()


def test_aggregate_install_refuses_open_admission(tmp_path, monkeypatch):
    from deploy.aliyun_20261111 import systemd

    marker = tmp_path / "admission"
    marker.touch()
    monkeypatch.setattr(systemd, "ADMISSION", marker)
    monkeypatch.setattr(systemd.subprocess, "check_output", lambda *a, **k: "inactive")
    with pytest.raises(ValueError, match="Remove runtime admission"):
        systemd.require_stopped()


def test_incident_hold_blocks_boot_and_restarts_before_requesting_stop(tmp_path, monkeypatch):
    from deploy.aliyun_20261111 import systemd

    marker = tmp_path / "admission"
    marker.touch()
    monkeypatch.setattr(systemd, "ADMISSION", marker)
    monkeypatch.setattr(systemd, "SYSTEMD_DIRECTORY", tmp_path)
    monkeypatch.setattr(systemd.os, "geteuid", lambda: 0)
    calls = []

    def run(args, **kwargs):
        assert not marker.exists()
        for name in systemd.SERVICES:
            text = (tmp_path / (name + ".d") / "90-incident-hold.conf").read_text()
            assert f"ConditionPathExists={marker}" in text and "Restart=no" in text
        calls.append(args)

    monkeypatch.setattr(systemd.subprocess, "run", run)
    systemd.hold()
    assert calls[0] == ["systemctl", "daemon-reload"]
    assert calls[1][:2] == ["systemctl", "disable"]
    assert calls[2][:3] == ["systemctl", "stop", "--no-block"]
    assert "freqtrade-live-selector.timer" in calls[1]
    assert set(systemd.SERVICES) <= set(calls[2])


def test_incident_hold_still_attempts_stop_after_reload_failure(tmp_path, monkeypatch):
    from deploy.aliyun_20261111 import systemd

    monkeypatch.setattr(systemd, "ADMISSION", tmp_path / "admission")
    monkeypatch.setattr(systemd, "SYSTEMD_DIRECTORY", tmp_path)
    monkeypatch.setattr(systemd.os, "geteuid", lambda: 0)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[1] == "daemon-reload":
            raise subprocess.TimeoutExpired(args, 20)

    monkeypatch.setattr(systemd.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="Incomplete hold"):
        systemd.hold()
    assert [args[1] for args in calls] == ["daemon-reload", "disable", "stop"]


def test_cache_compaction_preserves_exact_rows_and_backup(tmp_path):
    from deploy.aliyun_20261111.systemd import compact_cache

    path = tmp_path / "guard.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE budget(cooldown,strikes);
            INSERT INTO budget VALUES(2000000000,7);
            CREATE TABLE leases(ticket,expires);
            INSERT INTO leases VALUES('active',2000000000);
            CREATE TABLE public_cache(key,value,expires,owner,lease_until);
            INSERT INTO public_cache VALUES('keep','[]',1,'active',2000000000);
            CREATE TABLE disposable(value);
            INSERT INTO disposable VALUES(zeroblob(1048576));
            DROP TABLE disposable;
        """)
        before = list(conn.iterdump())
    result = compact_cache(path)
    assert result["after_bytes"] < result["before_bytes"]
    for candidate in (path, Path(result["backup"])):
        with sqlite3.connect(candidate) as conn:
            assert list(conn.iterdump()) == before
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
