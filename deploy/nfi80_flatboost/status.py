"""Read native local REST/SQLite state and emit only nonsecret paper evidence."""

import json
import sqlite3
from datetime import UTC, datetime

import requests

from deploy.nfi80_flatboost.launch import HERE, ROLES, STATE, read, verify


def snapshot():
    verify()
    result = {
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "accounts": {},
        "exchange_keys_loaded": False,
        "real_orders_enabled": False,
    }
    for role, strategy in ROLES.items():
        state = STATE / role
        runtime = read(state / "runtime.private.json")
        api = runtime["api_server"]
        session = requests.Session()
        session.trust_env = False
        session.auth = (api["username"], api["password"])
        base = f"http://127.0.0.1:{api['listen_port']}/api/v1/"

        def get(endpoint, session=session, base=base):
            response = session.get(base + endpoint, timeout=15)
            response.raise_for_status()
            return response.json()

        config = get("show_config")
        whitelist = get("whitelist")
        status = get("status")
        health = get("health")
        balance = get("balance")
        if config["dry_run"] is not True or config["strategy"] != strategy:
            raise ValueError("Native runtime is not the intended paper strategy")
        pairs = whitelist["whitelist"]
        expected = read(HERE / f"{role}-config.json")["exchange"]["pair_whitelist"]
        if pairs != expected or config["max_open_trades"] != 12:
            raise ValueError("Native runtime universe or capacity differs")
        with sqlite3.connect(f"file:{state / 'trades.sqlite'}?mode=ro", uri=True) as conn:
            integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
            trades = conn.execute("SELECT count(*) FROM trades").fetchone()[0]
            orders = conn.execute("SELECT count(*) FROM orders").fetchone()[0]
            lev = [r[0] for r in conn.execute("SELECT DISTINCT leverage FROM trades")]
        if integrity != "ok" or any(value != 3 for value in lev):
            raise ValueError("Paper ledger integrity or actual leverage mismatch")
        result["accounts"][role] = {
            "strategy": strategy,
            "dry_run": config["dry_run"],
            "state": config["state"],
            "pairs": len(pairs),
            "max_open_trades": 12,
            "paper_entry_start_utc": runtime["paper_entry_start"],
            "open_trades": len(status),
            "total_trades": trades,
            "orders": orders,
            "filled_leverages_observed": lev,
            "sqlite_integrity": integrity,
            "health": health,
            "balance": {
                k: balance[k] for k in ("total", "starting_capital", "trade_count") if k in balance
            },
        }
    (HERE / "runtime-receipt.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    snapshot()
