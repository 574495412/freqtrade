"""Prepare two isolated daily-pool paper accounts and the native selection scheduler."""

import json
import os
import plistlib
import secrets
import socket
from datetime import UTC, datetime

from deploy.nfi80_selected.launch import HERE, ROLES, ROOT, STATE, read, validate
from deploy.nfi80_selected.selector import SNAPSHOT, save, sha
from deploy.nfi80_selected.strategy.NFISelectedPaper import snapshot_pairs


PORTS = {"optimized": 18832}


def main():
    if not read(HERE / "selection-equivalence.json")["passed"]:
        raise ValueError("Historical selection equivalence must pass")
    if (HERE / "release.json").exists() or any((STATE / role).exists() for role in ROLES):
        raise FileExistsError("Do not overwrite a daily paper release or ledger")
    if not snapshot_pairs(SNAPSHOT, datetime.now(UTC), sha(HERE / "policy.json")):
        raise ValueError("Fresh successful native80-pair selection required")
    for port in PORTS.values():
        with socket.socket() as check:
            check.bind(("127.0.0.1", port))
    token_path = ROOT / "user_data/deployment/live-prepare-20261109/notification_token"
    if token_path.stat().st_mode & 0o077:
        raise ValueError("Notification token must remain private")
    token = token_path.read_text().strip()
    os.umask(0o077)
    start = datetime.now(UTC).isoformat()
    (HERE / "launchagents").mkdir(exist_ok=True)
    for role in ROLES:
        validate(read(HERE / f"{role}-config.json"), role)
        state = STATE / role
        state.mkdir(mode=0o700)
        save(
            state / "runtime.private.json",
            {
                "paper_entry_start": start,
                "api_server": {
                    "enabled": True,
                    "listen_ip_address": "127.0.0.1",
                    "listen_port": PORTS[role],
                    "verbosity": "error",
                    "enable_openapi": False,
                    "username": "nfi80-selected-" + role,
                    "password": secrets.token_urlsafe(32),
                    "jwt_secret_key": secrets.token_urlsafe(48),
                    "ws_token": secrets.token_urlsafe(32),
                    "CORS_origins": [],
                },
                "webhook": {"url": "http://127.0.0.1:18789/events/" + token},
            },
        )
    for role in [*ROLES, "selector"]:
        label = "io.freqtrade.paper.nfi80.selected." + role
        state = STATE / role
        state.mkdir(exist_ok=True)
        agent = {
            "Label": label,
            "ProgramArguments": [
                str(ROOT / ".venv-paper/bin/python"),
                str(HERE / "launch.py"),
                role,
            ],
            "WorkingDirectory": str(ROOT),
            "RunAtLoad": True,
            "KeepAlive": role != "selector",
            "ThrottleInterval": 30,
            "ExitTimeOut": 60 if role != "selector" else 900,
            "Umask": 63,
            "StandardOutPath": str(state / "service.stdout.log"),
            "StandardErrorPath": str(state / "service.stderr.log"),
        }
        if role == "selector":
            agent["StartInterval"] = 300
        (HERE / "launchagents" / (label + ".plist")).write_bytes(plistlib.dumps(agent))
    save(
        STATE / "creation.json",
        {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "paper_entry_start_utc": start,
            "virtual_usdt_each": 1000,
            "initial_snapshot_id": read(SNAPSHOT)["snapshot_id"],
            "existing_accounts_preserved": True,
        },
    )
    files = [
        "launch.py",
        "prepare.py",
        "selector.py",
        "selection-equivalence.json",
        "policy.json",
        "optimized-config.json",
        "strategy/NFISelectedPaper.py",
    ]
    files.extend(str(p.relative_to(HERE)) for p in (HERE / "launchagents").glob("*.plist"))
    save(
        HERE / "release.json",
        {
            "mode": "dry_run",
            "real_orders_enabled": False,
            "production_eligible": False,
            "paper_entry_start_utc": start,
            "files": {name: sha(HERE / name) for name in files},
            "source_parent_release": str(ROOT / "deploy/nfi80_flatboost/release.json"),
            "same_historical_rulebook_selection": True,
            "snapshot_expiry_seconds": 86400,
            "new_financial_backtest_run": False,
        },
    )
    print(
        json.dumps(
            {
                "prepared": True,
                "roles": list(ROLES),
                "common_start_utc": start,
                "snapshot_id": read(SNAPSHOT)["snapshot_id"],
            }
        )
    )


if __name__ == "__main__":
    main()
