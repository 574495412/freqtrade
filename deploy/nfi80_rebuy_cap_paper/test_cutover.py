# ruff: noqa: S101
"""Exercise the operator transaction without touching a machine or exchange."""

import json
import sqlite3

import pytest

from deploy.nfi80_rebuy_cap_paper import cutover


@pytest.fixture
def service(monkeypatch, tmp_path):
    admission = tmp_path / "admitted"
    admission.touch()
    dropin = tmp_path / "unit.d/candidate.conf"
    monkeypatch.setattr(cutover, "ADMISSION", admission)
    monkeypatch.setattr(cutover, "DEPLOYMENT", tmp_path)
    monkeypatch.setattr(cutover, "DROPIN", dropin)
    monkeypatch.setattr(cutover.os, "geteuid", lambda: 0)
    state = {"pid": 21, "events": []}

    def properties(unit, field):
        if field == "ExecStart":
            return (f"{cutover.SCRIPT} --worker futures" if dropin.exists()
                    else "deploy.mainnet_100_20261109.manual --worker futures")
        assert field == "MainPID"
        return str(state["pid"] if unit == cutover.UNIT else 10)

    def command(*args):
        state["events"].append(args)
        assert len(args) < 2 or args[1] != cutover.SPOT
        if args[0] == "stop":
            state["pid"] = 0

    def start(_, candidate):
        state["events"].append(("start", candidate))
        if candidate and state.get("skip_heartbeat"):
            state["pid"] = 24
            raise TimeoutError()
        state["pid"] = 22 if candidate else 23
        return state["pid"]

    monkeypatch.setattr(cutover, "output", lambda *args: "test-commit")
    monkeypatch.setattr(cutover, "property_value", properties)
    monkeypatch.setattr(cutover, "systemctl", command)
    monkeypatch.setattr(cutover, "start", start)
    monkeypatch.setattr(cutover, "backup_ledger", lambda _: "test-sha")
    monkeypatch.setattr(cutover, "exchange_status", lambda: {"positions": 2, "orders": 0})
    return state, dropin


def test_existing_positions_participate(service, tmp_path):
    state, dropin = service
    cutover.activate()
    data = json.loads(next(tmp_path.glob("nfi-cap-activation-*/result.json")).read_text())
    assert data["before_new_worker"] == {"positions": 2, "orders": 0}
    assert data["after"] == {"positions": 2, "orders": 0}
    assert data["new_pid"] == 22
    assert state["events"] == [("stop", cutover.UNIT), ("daemon-reload",), ("start", True)]
    assert dropin.exists()


def test_failed_candidate_rolls_back_on_current_ledger(service, tmp_path):
    state, dropin = service
    state["skip_heartbeat"] = True
    with pytest.raises(TimeoutError):
        cutover.activate()
    data = json.loads(next(tmp_path.glob("nfi-cap-activation-*/result.json")).read_text())
    assert state["pid"] == 23
    assert not dropin.exists()
    assert data["rollback"] == "OLD_WORKER_RUNNING"


def test_pending_order_leaves_original_running(service, monkeypatch):
    state, dropin = service
    monkeypatch.setattr(cutover, "exchange_status", lambda: {"positions": 2, "orders": 1})
    with pytest.raises(ValueError, match="open orders"):
        cutover.activate()
    assert not dropin.exists() and state["events"] == []


def test_native_sqlite_backup_preserves_existing_book(tmp_path, monkeypatch):
    monkeypatch.setattr(cutover, "ACCOUNT", tmp_path)
    with sqlite3.connect(tmp_path / "trades.sqlite") as db:
        db.execute("create table trades (id integer, is_open integer)")
        db.executemany("insert into trades values (?, ?)", [(1, 1), (2, 0)])
    target = tmp_path / "backup.sqlite"
    assert len(cutover.backup_ledger(target)) == 64
    with sqlite3.connect(target) as saved:
        assert saved.execute("select * from trades order by id").fetchall() == [(1, 1), (2, 0)]
