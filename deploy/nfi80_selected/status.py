"""Read native forward accounts, shared pool identity and optional fixed-pool baselines."""

import json
import sqlite3
from datetime import UTC, datetime

import requests

from deploy.nfi80_selected.launch import HERE, ROLES, ROOT, STATE, read, verify
from deploy.nfi80_selected.selector import SNAPSHOT


def query(state):
    api = read(state / "runtime.private.json")["api_server"]
    session = requests.Session()
    session.trust_env = False
    session.auth = (api["username"], api["password"])
    base = f"http://127.0.0.1:{api['listen_port']}/api/v1/"

    def get(endpoint):
        response = session.get(base + endpoint, timeout=15)
        response.raise_for_status()
        return response.json()

    config, whitelist, health, balance, trades = [
        get(k) for k in ("show_config", "whitelist", "health", "balance", "status")
    ]
    with sqlite3.connect(f"file:{state / 'trades.sqlite'}?mode=ro", uri=True) as conn:
        integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
        total_trades = conn.execute("SELECT count(*) FROM trades").fetchone()[0]
        orders = conn.execute("SELECT count(*) FROM orders").fetchone()[0]
    if not config["dry_run"] or integrity != "ok":
        raise ValueError("Native paper account mode or ledger failed")
    return {
        "strategy": config["strategy"],
        "state": config["state"],
        "dry_run": config["dry_run"],
        "max_open_trades": config["max_open_trades"],
        "active_pairs": whitelist["whitelist"],
        "held_pairs": [t["pair"] for t in trades],
        "open_trades": len(trades),
        "total_trades": total_trades,
        "orders": orders,
        "health": health,
        "balance_total": balance["total"],
        "sqlite_integrity": integrity,
    }


def main():
    verify()
    snapshot = read(SNAPSHOT)
    accounts = {}
    for role, strategy in ROLES.items():
        actual = query(STATE / role)
        if actual["strategy"] != strategy or actual["max_open_trades"] != 12:
            raise ValueError("Unexpected daily strategy or capacity")
        if set(actual["active_pairs"]) != set(snapshot["pairs"]) | set(actual["held_pairs"]):
            raise ValueError("Active pool differs from snapshot plus managed holdings")
        accounts[role] = actual
    fixed = {
        role: query(ROOT / "user_data/deployment/nfi80-flatboost-20261109" / role) for role in ROLES
    }
    now = datetime.now(UTC)
    result = {
        "observed_at_utc": now.isoformat(),
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_expires_utc": snapshot["valid_until_utc"],
        "snapshot_is_fresh": now < datetime.fromisoformat(snapshot["valid_until_utc"]),
        "daily_accounts": accounts,
        "fixed_pool_accounts": fixed,
        "real_orders_enabled": False,
        "exchange_keys_loaded": False,
    }
    (HERE / "runtime-receipt.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "snapshot_id": result["snapshot_id"],
                "snapshot_is_fresh": result["snapshot_is_fresh"],
                "daily": {
                    role: {k: v for k, v in value.items() if k != "active_pairs"}
                    for role, value in accounts.items()
                },
                "fixed": {
                    role: {
                        k: value[k]
                        for k in ("state", "open_trades", "total_trades", "balance_total")
                    }
                    for role, value in fixed.items()
                },
            }
        )
    )


if __name__ == "__main__":
    main()
