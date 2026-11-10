"""Repair only an existing, flat, user-authorized spot worker's metadata scope.

--prepare validates an isolated candidate. --apply performs the explicitly
user-authorized maintenance restart; it never creates a test/forced order.
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import psutil

from deploy.mainnet_100_20261109.prepare import HERE, ROOT, STATE
from deploy.mainnet_100_20261109.spot_transport import scoped_config
from deploy.nfi80_selected.selector import read, save, sha


def ledger_counts(path):
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
        return {
            name: connection.execute(f"select count(*) from {name}").fetchone()[0]
            for name in ("trades", "orders")
        }


def prepare():
    os.umask(0o077)
    spot = STATE / "spot"
    cfg = read(spot / "config.json")
    release = read(HERE / "release.json")
    if sha(spot / "config.json") != release["config_sha256"]["spot"]:
        raise ValueError("Existing spot config differs from its release")
    candidate = scoped_config(cfg)
    # Exact scope check: remove only the new transport options, and compare all
    # other config fields, including strategy, pairs, amounts and webhook config.
    restored = json.loads(json.dumps(candidate))
    for name in ("ccxt_config", "ccxt_async_config"):
        previous = cfg["exchange"].get(name, {}).get("options", {})
        options = restored["exchange"][name]["options"]
        for key in ("fetchMarkets", "fetchMargins"):
            if key in previous:
                options[key] = previous[key]
            else:
                options.pop(key)
    if restored != cfg:
        raise ValueError("Unexpected change outside spot metadata routing")
    from freqtrade.configuration.config_validation import validate_config_consistency
    from freqtrade.enums import RunMode
    from freqtrade.resolvers import StrategyResolver

    with TemporaryDirectory(prefix="nfi-spot-transport-schema-") as folder:
        safe = json.loads(json.dumps(candidate))
        safe.update(user_data_dir=Path(folder), runmode=RunMode.LIVE)
        safe["webhook"] = {"enabled": False, "url": "http://127.0.0.1/unused"}
        StrategyResolver.load_strategy(safe)
        validate_config_consistency(safe)
    folder = STATE / ("spot-transport-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"))
    folder.mkdir(mode=0o700)
    shutil.copy2(spot / "config.json", folder / "config-before.private.json")
    shutil.copy2(HERE / "release.json", folder / "release-before.json")
    save(folder / "config-candidate.private.json", candidate)
    result = {
        "prepared_at_utc": datetime.now(UTC).isoformat(),
        "private_artifacts": str(folder),
        "before_config_sha256": sha(spot / "config.json"),
        "candidate_config_sha256": sha(folder / "config-candidate.private.json"),
        "only_metadata_routing_changed": True,
        "native_schema_passed": True,
        "capital_ceiling_usdt": 100,
        "stake_budget_usdt": 99,
        "configured_spot_pairs": 36,
        "workers_restarted": 0,
        "orders_forced": 0,
    }
    save(folder / "preparation.json", result)
    print(json.dumps(result, indent=2))
    return folder, candidate


def apply():
    os.umask(0o077)
    folder, candidate = prepare()
    spot = STATE / "spot"
    db = spot / "trades.sqlite"
    if ledger_counts(db) != {"trades": 0, "orders": 0}:
        raise ValueError("This scoped repair only supports the observed flat ledger")
    old = psutil.Process(read(spot / "worker.pid")["pid"])
    if old.cmdline()[-2:] != ["--worker", "spot"] or Path(old.cwd()).resolve() != ROOT:
        raise ValueError("Spot PID identity mismatch")
    # SIGTERM follows the owning native graceful cleanup path; do not kill or
    # reset database/cache files. No other service/PID is selected.
    old.terminate()
    old.wait(timeout=45)
    backup = folder / "trades-before.sqlite"
    with sqlite3.connect(db) as source, sqlite3.connect(backup) as target:
        source.backup(target)
        if list(source.iterdump()) != list(target.iterdump()):
            raise ValueError("Native ledger backup mismatch")
    backup.chmod(0o600)
    for path in spot.glob("nfix8-profit_max-*.json"):
        shutil.copy2(path, folder / path.name)
    # Reconcile after graceful stop so no order can race this account snapshot.
    from deploy.mainnet_100_20261109.spot_status import observe

    check = observe()
    if not check["flat_state_snapshot_reconciled"]:
        raise ValueError("Post-stop account state requires reconciliation")
    save(spot / "config.json", candidate)
    release = read(HERE / "release.json")
    release["config_sha256"]["spot"] = sha(spot / "config.json")
    for name in ("prepare.py", "spot_transport.py", "spot_restart.py"):
        release["files"][name] = sha(HERE / name)
    save(HERE / "release.json", release)
    from deploy.mainnet_100_20261109.manual import verify

    verify()
    environment = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "LC_ALL", "TZ")
        if key in os.environ
    }
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    with (spot / "manual.stdout.log").open("ab") as log:
        child = subprocess.Popen(
            [sys.executable, "-B", "-m", "deploy.mainnet_100_20261109.manual", "--worker", "spot"],
            cwd=ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    save(spot / "worker.pid", {"pid": child.pid})
    receipt = {
        "requested_at_utc": datetime.now(UTC).isoformat(),
        "authorization": (
            "User allowed post-launch fixes/restarts and spot runtime/fill reconciliation"
        ),
        "old_pid": old.pid,
        "new_pid": child.pid,
        "market": "spot",
        "workers_restarted": 1,
        "original_ledger_preserved": True,
        "backup_sql_dump_equal": True,
        "private_artifacts": str(folder),
        "post_stop_account_flat": True,
        "config_sha256": sha(spot / "config.json"),
        "capital_ceiling_usdt": 100,
        "stake_budget_usdt": 99,
        "orders_forced": 0,
        "futures_service_modified": False,
        "status": "START_REQUESTED_PENDING_NATIVE_HEALTH_AND_FILL_EVIDENCE",
    }
    save(HERE / "spot-restart-receipt.json", receipt)
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        apply() if args.apply else prepare()
    except Exception as exc:
        raise SystemExit("Spot transport maintenance stopped: " + type(exc).__name__) from None
