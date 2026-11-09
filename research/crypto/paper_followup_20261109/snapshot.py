"""Freeze read-only native paper ledgers before requesting public valuation inputs."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
RAW = ROOT / "user_data/crypto_quant/paper_followup_20261109_v001"


def ref(path):
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    from deploy.launch import verify

    verify()
    RAW.mkdir(exist_ok=False)
    now = datetime.now(UTC)
    end = now.replace(second=0, microsecond=0) - timedelta(minutes=2)
    start = datetime(2026, 11, 6, 17, tzinfo=UTC)
    accounts, instruments = {}, {}
    for market in ("spot", "futures"):
        for version in ("original", "addcap"):
            base = ROOT / "user_data/deployment"
            folder = base / market if version == "original" else base / "nfi-x8-addcap" / market
            config_path = (
                ROOT / "deploy/configs" / f"{market}.json"
                if version == "original"
                else folder / "config.json"
            )
            config = json.loads(config_path.read_text())
            if config["dry_run"] is not True or config["dry_run_wallet"] != 1000:
                raise ValueError("Only the existing1000USDT paper contract is supported")
            with sqlite3.connect(
                (folder / "trades.sqlite").as_uri() + "?mode=ro", uri=True
            ) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only=ON")
                conn.execute("BEGIN")
                if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("Native paper ledger integrity failure")
                trades = [dict(row) for row in conn.execute("SELECT * FROM trades ORDER BY id")]
                orders = [dict(row) for row in conn.execute("SELECT * FROM orders ORDER BY id")]
            key = f"{market}_{version}"
            path = RAW / f"{key}_ledger.json"
            save(path, {"captured_at_utc": now.isoformat(), "trades": trades, "orders": orders})
            config_copy = RAW / f"{key}_config.json"
            config_copy.write_bytes(config_path.read_bytes())
            accounts[key] = {
                "ledger": ref(path),
                "config": ref(config_copy),
                "market": market,
                "version": version,
                "initial_usdt": config["dry_run_wallet"],
                "fee": config["fee"],
            }
            for trade in trades:
                closing = (
                    datetime.fromisoformat(trade["close_date"]).replace(tzinfo=UTC)
                    if trade["close_date"]
                    else None
                )
                if closing and closing < start:
                    continue
                symbol = trade["pair"].split(":")[0].replace("/", "")
                instruments[f"{market}_{symbol}"] = {"market": market, "symbol": symbol}
    save(
        RAW / "snapshot.json",
        {
            "captured_at_utc": now.isoformat(),
            "end": end.isoformat(),
            "start": start.isoformat(),
            "post_repair_start": "2026-11-07T15:30:00+00:00",
            "accounts": accounts,
            "instruments": instruments,
            "native_trade_model": ref(ROOT / "freqtrade/persistence/trade_model.py"),
            "purpose": (
                "Native actual-fill paper account observation; "
                "not a new backtest, strategy simulation or promotion."
            ),
            "native_strategy_and_release": ref(ROOT / "deploy/release.json"),
            "orders_enabled": False,
        },
    )
    print(
        json.dumps(
            {
                "raw": str(RAW),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "instruments": list(instruments),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
