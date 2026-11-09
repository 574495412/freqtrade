"""Freeze an anonymous paper release and create two fresh private account directories."""

import hashlib
import json
import os
import plistlib
import secrets
import socket
from datetime import UTC, datetime

from deploy.nfi80_flatboost.launch import HERE, ROLES, ROOT, STATE, read, validate_paper


PORTS = {"optimized": 18812, "control": 18813}


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def main():
    if read(HERE / "parity-result.json")["passed"] is not True:
        raise ValueError("Paper financial parity has not passed")
    if STATE.exists() or (HERE / "release.json").exists():
        raise FileExistsError("Existing account/release must not be overwritten")
    for port in PORTS.values():
        with socket.socket() as check:
            check.bind(("127.0.0.1", port))
    token_file = ROOT / "user_data/deployment/live-prepare-20261109/notification_token"
    if token_file.stat().st_mode & 0o077:
        raise ValueError("Notification relay token must remain private")
    token = token_file.read_text().strip()
    os.umask(0o077)
    STATE.mkdir(mode=0o700)
    start_epoch = (int(datetime.now(UTC).timestamp()) // 300 + 2) * 300
    start = datetime.fromtimestamp(start_epoch, UTC).isoformat()
    (HERE / "launchagents").mkdir(exist_ok=True)
    for role in ROLES:
        validate_paper(read(HERE / f"{role}-config.json"), role)
        state = STATE / role
        state.mkdir(mode=0o700)
        runtime = {
            "paper_entry_start": start,
            "api_server": {
                "enabled": True,
                "listen_ip_address": "127.0.0.1",
                "listen_port": PORTS[role],
                "verbosity": "error",
                "enable_openapi": False,
                "username": "nfi80-" + role,
                "password": secrets.token_urlsafe(32),
                "jwt_secret_key": secrets.token_urlsafe(48),
                "ws_token": secrets.token_urlsafe(32),
                "CORS_origins": [],
            },
            "webhook": {"url": "http://127.0.0.1:18789/events/" + token},
        }
        save(state / "runtime.private.json", runtime)
        label = "io.freqtrade.paper.nfi80." + role
        agent = {
            "Label": label,
            "ProgramArguments": [
                str(ROOT / ".venv-paper/bin/python"),
                str(HERE / "launch.py"),
                role,
            ],
            "WorkingDirectory": str(ROOT),
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 30,
            "ExitTimeOut": 60,
            "Umask": 63,
            "StandardOutPath": str(state / "service.stdout.log"),
            "StandardErrorPath": str(state / "service.stderr.log"),
        }
        (HERE / "launchagents" / (label + ".plist")).write_bytes(plistlib.dumps(agent))
    save(
        STATE / "creation.json",
        {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "paper_entry_start_utc": start,
            "virtual_usdt_per_account": 1000,
            "new_accounts": list(ROLES),
            "copied_exchange_credentials": False,
            "preserved_existing_accounts": True,
        },
    )
    names = [
        "launch.py",
        "prepare.py",
        "optimized-config.json",
        "control-config.json",
        "strategy/NFIFlatTopBoostPaper.py",
        "parity-result.json",
    ]
    names.extend(str(p.relative_to(HERE)) for p in (HERE / "launchagents").glob("*.plist"))
    save(
        HERE / "release.json",
        {
            "mode": "dry_run",
            "real_orders_enabled": False,
            "production_eligible": False,
            "upstream_commit": "c051e9065c5b677f03817dd1f83cb980af1ed684",
            "strategy_commit": "1af55971d3a64703ec449078e09fdec257d0d3c6",
            "files": {
                name: hashlib.sha256((HERE / name).read_bytes()).hexdigest() for name in names
            },
            "paper_entry_start_utc": start,
            "independent_virtual_wallets": True,
            "research_drawdown_screen_pct": 30,
            "screen_is_not_automatic_liquidation_rule": True,
        },
    )
    print(
        json.dumps(
            {
                "prepared": True,
                "paper_accounts": list(ROLES),
                "paper_entry_start_utc": start,
                "exchange_keys_loaded": False,
            }
        )
    )


if __name__ == "__main__":
    main()
