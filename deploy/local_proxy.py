"""Apply an explicit loopback proxy to the four existing paper accounts, while stopped."""

import fcntl
import hashlib
import json
import os
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "user_data/deployment/network-proxy-20261107"
PROXY = "http://127.0.0.1:7897"
LABELS = (
    "io.freqtrade.paper.spot",
    "io.freqtrade.paper.futures",
    "io.freqtrade.paper.x8-addcap.spot",
    "io.freqtrade.paper.x8-addcap.futures",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():  # noqa: C901 -- one sequential, guarded maintenance transaction
    from deploy.launch import verify

    verify()
    for label in LABELS:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode == 0:
            raise ValueError("Unload each existing paper service before changing its configuration")
    os.umask(0o077)
    locks = []
    for market in ("spot", "futures"):
        for base in (ROOT / "user_data/deployment", ROOT / "user_data/deployment/nfi-x8-addcap"):
            lock = (base / market / "worker.lock").open("a")
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locks.append(lock)
    STATE.mkdir(exist_ok=False)
    plan, configs = [], []
    old_release = ROOT / "deploy/release.json"
    (STATE / "release.before.json").write_bytes(old_release.read_bytes())
    for market in ("spot", "futures"):
        for version in ("original", "addcap"):
            folder = (
                ROOT / "user_data/deployment" / market
                if version == "original"
                else ROOT / "user_data/deployment/nfi-x8-addcap" / market
            )
            path = (
                ROOT / "deploy/configs" / f"{market}.json"
                if version == "original"
                else folder / "config.json"
            )
            old = json.loads(path.read_text())
            if old["dry_run"] is not True or old["dry_run_wallet"] != 1000:
                raise ValueError("Only the existing 1000 USDT paper accounts may be changed")
            if any(old["exchange"].get(k) for k in ("key", "secret", "password")):
                raise ValueError("Paper transport change must not load account credentials")
            before = STATE / f"{market}-{version}.before.json"
            before.write_bytes(path.read_bytes())
            new = json.loads(path.read_text())
            ccxt = new["exchange"].setdefault("ccxt_config", {})
            if ccxt.get("httpsProxy") not in (None, PROXY):
                raise ValueError("Refuse to replace a different explicit proxy")
            ccxt["httpsProxy"] = PROXY
            stripped = json.loads(json.dumps(new))
            stripped["exchange"]["ccxt_config"].pop("httpsProxy")
            if stripped != old:
                raise ValueError("Transport patch must change only exchange.ccxt_config.httpsProxy")
            backup = STATE / f"{market}-{version}.sqlite"
            with sqlite3.connect(
                (folder / "trades.sqlite").resolve().as_uri() + "?mode=ro", uri=True
            ) as source:
                with sqlite3.connect(backup) as target:
                    source.backup(target)
                    if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("Paper ledger backup failed integrity check")
                    source_dump = "\n".join(source.iterdump()).encode()
                    target_dump = "\n".join(target.iterdump()).encode()
                    if source_dump != target_dump:
                        raise ValueError("Paper ledger backup logical dump mismatch")
                    counts = {
                        table: source.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                        for table in ("trades", "orders")
                    }
            configs.append((path, new))
            plan.append(
                {
                    "market": market,
                    "version": version,
                    "path": str(path),
                    "before_sha256": digest(before),
                    "backup_config": str(before),
                    "backup_db": str(backup),
                    "backup_db_sha256": digest(backup),
                    "logical_db_sha256": hashlib.sha256(source_dump).hexdigest(),
                    "native_counts_before_restart": counts,
                }
            )
    save(
        STATE / "prepared.json",
        {
            "at_utc": datetime.now(UTC).isoformat(),
            "configurations": plan,
            "proxy": PROXY,
            "only_transport_change": True,
            "dry_run": True,
            "orders_enabled": False,
        },
    )
    for path, new in configs:
        temporary = path.with_name(path.name + ".proxy-tmp")
        if temporary.exists() or path.is_symlink():
            raise ValueError("Unsafe configuration destination")
        save(temporary, new)
        temporary.replace(path)
    release = json.loads(old_release.read_text())
    for market in ("spot", "futures"):
        name = f"deploy/configs/{market}.json"
        release["files"][name] = digest(ROOT / name)
    release["files"]["deploy/local_proxy.py"] = digest(Path(__file__))
    release["transport_revision"] = {
        "at_utc": datetime.now(UTC).isoformat(),
        "previous_release_sha256": digest(STATE / "release.before.json"),
        "reason": (
            "CCXT ignores the system proxy by default; explicit loopback httpsProxy "
            "restores exchange connectivity."
        ),
        "signals_costs_universe_leverage_unchanged": True,
        "existing_ledgers_preserved": True,
    }
    save(old_release, release)
    verify()
    for row in plan:
        row["after_sha256"] = digest(Path(row["path"]))
    save(
        STATE / "applied.json",
        {
            "at_utc": datetime.now(UTC).isoformat(),
            "configurations": plan,
            "release_sha256": digest(old_release),
            "proxy": PROXY,
            "dry_run": True,
            "real_orders_enabled": False,
            "rollback": (
                "Unload the same four paper services; restore all four backup configs "
                "and release.before.json. Keep the latest native databases; "
                "do not roll back trading state."
            ),
        },
    )
    print(
        json.dumps(
            {
                "patched_paper_configurations": len(plan),
                "proxy": PROXY,
                "mode": "dry_run",
                "native_ledgers_backed_up_and_retained": True,
            }
        )
    )


if __name__ == "__main__":
    main()
