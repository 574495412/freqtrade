"""Operator-run cutover to the Git-pinned NFI 14-slot futures strategy."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from time import monotonic, sleep
from zoneinfo import ZoneInfo


SCRIPT = Path(__file__).resolve()
RELEASE = SCRIPT.parents[2]
ROOT = Path("/opt/freqtrade")
PYTHON = ROOT / ".venv-paper/bin/python"
DEPLOYMENT = ROOT / "user_data/deployment"
ACCOUNT = DEPLOYMENT / "mainnet-100-20261109/futures"
UNIT = "freqtrade-live-futures.service"
SPOT = "freqtrade-live-spot.service"
DROPIN = Path(f"/etc/systemd/system/{UNIT}.d/95-nfi-rebuy-cap.conf")
ADMISSION = Path("/run/freqtrade-live-admitted")
STRATEGY = "NFIManualFlatBoostRebuyCap"
VERSION_SUFFIX = "-14slots-short-rebuy-cap"
UPSTREAM = (
    "deploy/mainnet_100_20261109/manual.py",
    "deploy/mainnet_100_20261109/strategy/NFIManualFlatBoost.py",
    "deploy/nfi80_flatboost/strategy/NFIFlatTopBoostPaper.py",
    "deploy/strategy/nfi-x8/NostalgiaForInfinityX8.py",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def output(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def systemctl(*args, timeout=360):
    subprocess.run(["systemctl", *args], check=True, timeout=timeout)


def property_value(unit, field):
    return output("systemctl", "show", unit, "-p", field, "--value")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def settings():
    config = json.loads((ACCOUNT / "config.json").read_text())
    require(
        config["strategy"] == "NFIManualFlatBoost"
        and config["max_open_trades"] == 12
        and config["dry_run"] is False
        and config["initial_state"] == "stopped"
        and config["trading_mode"] == "futures"
        and config["margin_mode"] == "isolated"
        and config["stake_amount"] == "unlimited"
        and config["live_capital_mode"] == "available_balance"
        and set(config["nfi_parameters"].values()) == {3.0}
        and config["db_url"] == "sqlite:///" + str(ACCOUNT / "trades.sqlite")
        and not config.get("cancel_open_orders_on_exit", False),
        "The live futures account differs from the verified contract",
    )
    return config


def precheck():
    from deploy.mainnet_100_20261109.manual import verify
    from deploy.nfi80_selected.strategy.NFISelectedPaper import snapshot_pairs
    from freqtrade.configuration.config_validation import validate_config_consistency
    from freqtrade.enums import RunMode
    from freqtrade.resolvers import StrategyResolver

    verify()
    require(RELEASE != ROOT, "Use a dedicated Git release checkout")
    require(not output("git", "-c", f"safe.directory={RELEASE}", "-C", str(RELEASE),
                       "status", "--porcelain"),
            "The Git release checkout must be clean")
    for relative in UPSTREAM:
        require(digest(ROOT / relative) == digest(RELEASE / relative),
                f"Installed worker dependency differs from Git: {relative}")
    config = settings()
    now = datetime.now(UTC)
    require(snapshot_pairs(config["daily_snapshot_file"], now, config["daily_policy_sha256"]),
            "A current verified 80-pair snapshot is required")
    for name in ("api_server", "telegram", "webhook"):
        config.pop(name, None)
    with TemporaryDirectory(prefix="nfi-live-resolve-") as scratch:
        config.update(strategy=STRATEGY, strategy_path=str(SCRIPT.parent / "strategy"),
                      user_data_dir=Path(scratch), max_open_trades=14, runmode=RunMode.LIVE)
        instance = StrategyResolver.load_strategy(config)
        validate_config_consistency(config)
        require(instance.__class__.__name__ == STRATEGY, "Incorrect strategy from native resolver")


def inspect_account():
    from deploy.runtime_recovery_20261111.preflight import observe

    try:
        with TemporaryDirectory(prefix="nfi-cap-reconcile-") as directory:
            record = observe("futures", Path(directory))
    except Exception as error:
        raise SystemExit(f"Exchange reconciliation failed: {type(error).__name__}") from None
    print(json.dumps({"positions": len(record["positions_reconciled"]),
                      "orders": record["exchange_open_order_count"]}))


def exchange_status():
    # Use the same permissions, request admission and exchange transport as the live worker.
    for attempt in range(3):
        try:
            raw = output("runuser", "-u", "freqtrade", "--", str(PYTHON), "-B",
                         str(SCRIPT), "--observe")
            return json.loads(raw.splitlines()[-1])
        except subprocess.CalledProcessError:
            if attempt == 2:
                raise
            sleep(3)


def backup_ledger(destination):
    ledger = ACCOUNT / "trades.sqlite"
    with closing(sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True)) as before:
        with closing(sqlite3.connect(destination)) as saved:
            before.backup(saved)
            require(saved.execute("PRAGMA integrity_check").fetchone() == ("ok",),
                    "Ledger backup integrity check failed")
    return digest(destination)


def worker():
    from deploy.mainnet_100_20261109.capital import wallet_class
    from deploy.mainnet_100_20261109.manual import save, verify
    from freqtrade import freqtradebot
    from freqtrade.main import main as native_main

    verify()
    config = settings()
    os.umask(0o077)
    with os.fdopen(os.open(ACCOUNT / "worker.lock", os.O_RDWR | os.O_NOFOLLOW), "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        save(ACCOUNT / "worker.pid", {"pid": os.getpid()})
        freqtradebot.Wallets = wallet_class(config)
        with TemporaryDirectory(prefix="nfi-cap-worker-") as directory:
            overlay = Path(directory) / "run.json"
            save(overlay, {"strategy": STRATEGY, "max_open_trades": 14, "initial_state": "running"})
            native_main([
                "trade", "--config", str(ACCOUNT / "config.json"),
                "--config", str(ACCOUNT / "credentials.json"), "--config", str(overlay),
                "--strategy-path", str(SCRIPT.parent / "strategy"),
                "--userdir", str(ACCOUNT), "--logfile", str(ACCOUNT / "freqtrade.log"),
            ])


def wait_running(previous_pid, candidate, since):
    log = ACCOUNT / "freqtrade.log"
    pattern = re.compile(r"Bot heartbeat. PID=(\d+),.*state='RUNNING'")
    expires = monotonic() + 360
    while monotonic() < expires:
        pid = int(property_value(UNIT, "MainPID"))
        if property_value(UNIT, "ActiveState") in {"failed", "inactive"}:
            raise ValueError("Futures service exited before a fresh heartbeat")
        if pid and pid != previous_pid:
            with log.open("rb") as stream:
                stream.seek(max(0, log.stat().st_size - 128 * 1024))
                lines = stream.read().decode(errors="replace").splitlines()
            for line in reversed(lines):
                match = pattern.search(line)
                if not match or int(match[1]) != pid or (VERSION_SUFFIX in line) != candidate:
                    continue
                timestamp = datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S,%f")
                if timestamp.replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() >= since:
                    return pid
        sleep(3)
    raise TimeoutError("Futures service did not produce a fresh RUNNING heartbeat")


def start(previous_pid, candidate):
    since = datetime.now(UTC).timestamp()
    systemctl("start", UNIT)
    return wait_running(previous_pid, candidate, since)


def rollback(previous_pid):
    if DROPIN.exists():
        systemctl("stop", UNIT)
        DROPIN.unlink()
        systemctl("daemon-reload")
    if int(property_value(UNIT, "MainPID")) == 0:
        # The old worker resumes the current ledger, including any intervening fills.
        exchange_status()
        systemctl("reset-failed", UNIT)
        start(previous_pid, candidate=False)


def activate():
    require(os.geteuid() == 0, "Run the operator command as root")
    # Release the resolver process and its memory before starting the worker.
    output("runuser", "-u", "freqtrade", "--", str(PYTHON), "-B", str(SCRIPT), "--check")
    require(ADMISSION.is_file(), "Existing service admission is inactive")
    require(not DROPIN.exists(), "A candidate drop-in is already installed")
    original = property_value(UNIT, "ExecStart")
    require("deploy.mainnet_100_20261109.manual --worker futures" in original,
            "Unexpected live futures entrypoint")
    old_pid, spot_pid = (int(property_value(unit, "MainPID")) for unit in (UNIT, SPOT))
    require(old_pid > 0 and spot_pid > 0, "Both resident services must already be active")
    before = exchange_status()
    require(before["orders"] == 0, "Existing open orders must settle before the restart")

    directory = Path(mkdtemp(prefix="nfi-cap-activation-", dir=DEPLOYMENT))
    result = directory / "result.json"
    report = {"old_pid": old_pid, "spot_pid": spot_pid, "before": before,
              "git_commit": output("git", "-C", str(RELEASE), "rev-parse", "HEAD")}
    try:
        systemctl("stop", UNIT)
        require(int(property_value(UNIT, "MainPID")) == 0, "Old worker did not exit")
        after_stop = exchange_status()
        require(after_stop["orders"] == 0, "An order appeared while the old worker was stopping")
        report["ledger_backup_sha256"] = backup_ledger(directory / "trades.sqlite")
        report["before_new_worker"] = after_stop
        DROPIN.parent.mkdir(parents=True, exist_ok=True)
        with DROPIN.open("x") as config:
            entrypoint = f"{PYTHON} -B {SCRIPT} --worker futures"
            config.write(f"[Service]\nExecStart=\nExecStart={entrypoint}\n")
        systemctl("daemon-reload")
        require(f"{SCRIPT} --worker futures" in property_value(UNIT, "ExecStart"),
                "systemd did not apply the candidate command")
        report["new_pid"] = start(old_pid, candidate=True)
        report["after"] = exchange_status()
        require(spot_pid == int(property_value(SPOT, "MainPID")), "The spot process changed")
        report["status"] = "RUNNING"
    except BaseException as error:
        report["status"] = "ACTIVATION_FAILED"
        report["failure"] = type(error).__name__
        try:
            rollback(old_pid)
            report["rollback"] = "OLD_WORKER_RUNNING"
        except Exception as rollback_error:
            report["rollback"] = type(rollback_error).__name__
        raise
    finally:
        result.write_text(json.dumps(report, indent=2) + "\n")
        print(result, flush=True)


def main():
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    for mode in ("check", "activate", "observe"):
        modes.add_argument(f"--{mode}", action="store_true")
    # The existing native health monitor identifies the market from this argument.
    modes.add_argument("--worker", choices=("futures",))
    args = parser.parse_args()
    if args.check:
        precheck()
        print("CHECK_OK No service changed")
    elif args.activate:
        lock_path = "/run/lock/freqtrade-nfi-cutover.lock"
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            activate()
    elif args.worker:
        worker()
    else:
        inspect_account()


if __name__ == "__main__":
    main()
