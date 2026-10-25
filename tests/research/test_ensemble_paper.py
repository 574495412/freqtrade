"""Reject a partial or differently funded paper portfolio before starting either child."""

import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from research.crypto.prepare_ensemble_paper import MASTER, STOPPER


@pytest.mark.parametrize("change", ["shared_database", "capital", "second_credentials"])
def test_master_rejects_invalid_portfolio_before_any_child_starts(tmp_path, change):
    components = []
    for name in ("nfi", "trend"):
        folder = tmp_path / name
        folder.mkdir()
        database = "shared" if change == "shared_database" else name
        config = {
            "dry_run": True,
            "dry_run_wallet": 1000,
            "db_url": "sqlite:///" + str(tmp_path / (database + ".sqlite")),
            "exchange": {"key": "", "secret": ""},
        }
        if change == "second_credentials" and name == "trend":
            config["exchange"]["secret"] = "test-only"
        (folder / "settings.json").write_text(json.dumps(config))
        (folder / "package.json").write_text("{}")
        (folder / "run_paper.py").write_text("raise SystemExit(0)\n")
        components.append(
            {
                "directory": name,
                "settings_sha256": hashlib.sha256(
                    (folder / "settings.json").read_bytes()
                ).hexdigest(),
                "package_sha256": hashlib.sha256(
                    (folder / "package.json").read_bytes()
                ).hexdigest(),
                "launcher_sha256": hashlib.sha256(
                    (folder / "run_paper.py").read_bytes()
                ).hexdigest(),
            }
        )
    master = tmp_path / "run_portfolio_paper.py"
    master.write_text(MASTER)
    manifest = {
        "mode": "paper_fixed_rule_control",
        "real_orders_enabled": False,
        "initial_equity_usdt": 1000 if change == "capital" else 2000,
        "master_sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
        "components": components,
    }
    (tmp_path / "package.json").write_text(json.dumps(manifest))
    env = {k: v for k, v in os.environ.items() if not k.startswith("FREQTRADE__")}
    result = subprocess.run(
        [sys.executable, str(master)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode != 0
    message = {
        "shared_database": "cannot share a trade database",
        "capital": "capital differs from the reviewed portfolio",
        "second_credentials": "no trading credentials",
    }
    assert message[change] in result.stderr
    assert not (tmp_path / "sessions").exists()


def test_single_owner_survives_master_death_and_stopper_only_stops_owned_children(tmp_path):
    """Both native-style exec children must retain ownership after their parent dies."""
    components = []
    for name in ("nfi", "trend"):
        folder = tmp_path / name
        folder.mkdir()
        config = {
            "dry_run": True,
            "dry_run_wallet": 1000,
            "db_url": "sqlite:///" + str(folder / "paper.sqlite"),
            "exchange": {},
        }
        (folder / "settings.json").write_text(json.dumps(config))
        (folder / "package.json").write_text("{}")
        (folder / "run_paper.py").write_text(
            "import os, sys, time\n"
            "if 'PAPER_LOCK_EXEC_TEST' not in os.environ:\n"
            "    os.execve(sys.executable, [sys.executable, __file__], "
            "dict(os.environ, PAPER_LOCK_EXEC_TEST='1'))\n"
            "time.sleep(60)\n"
        )
        components.append(
            {
                "directory": name,
                **{
                    key: hashlib.sha256((folder / file).read_bytes()).hexdigest()
                    for key, file in (
                        ("settings_sha256", "settings.json"),
                        ("package_sha256", "package.json"),
                        ("launcher_sha256", "run_paper.py"),
                    )
                },
            }
        )
    master = tmp_path / "run_portfolio_paper.py"
    master.write_text(MASTER)
    stopper = tmp_path / "stop_portfolio_paper.py"
    stopper.write_text(STOPPER)
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "mode": "paper_fixed_rule_control",
                "real_orders_enabled": False,
                "initial_equity_usdt": 2000,
                "master_sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
                "components": components,
            }
        )
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("FREQTRADE__")}
    process = subprocess.Popen(
        [sys.executable, str(master)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    children = []
    try:
        deadline = time.monotonic() + 5
        while not list((tmp_path / "sessions").glob("*.started.json")):
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.02)
        receipt = json.loads(next((tmp_path / "sessions").glob("*.started.json")).read_text())
        children = [child["pid"] for child in receipt["children"]]
        assert receipt["master_pid"] == process.pid
        for orphaned in (False, True):
            if orphaned:
                process.kill()
                process.wait(timeout=5)
            duplicate = subprocess.run(
                [sys.executable, str(master)],
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            assert duplicate.returncode != 0
            assert "already running" in duplicate.stderr
            assert len(list((tmp_path / "sessions").glob("*.started.json"))) == 1
        stopped = subprocess.run(
            [sys.executable, str(stopper)],
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert stopped.returncode == 0, stopped.stderr
        assert all(str(pid) in stopped.stdout for pid in children)
        with (tmp_path / ".portfolio.lock").open("a+") as lock:
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    assert time.monotonic() < deadline
                    time.sleep(0.02)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for pid in children:
            try:
                os.kill(pid, signal.SIGINT)
            except ProcessLookupError:
                pass
