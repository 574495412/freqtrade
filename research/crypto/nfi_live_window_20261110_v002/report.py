"""Validate native artifacts and compare only the matching live-ledger window."""

import io
import json
import sqlite3
import zipfile
from contextlib import closing
from datetime import UTC, datetime

import pandas as pd
import psutil

from research.crypto.nfi_live_window_20261110_v002.replay import (
    END,
    HERE,
    OUT,
    ROOT,
    START,
    save,
    sha,
)


def native_evidence(market, contract):
    folder = OUT / market / "native-run-verified"
    result = json.loads((folder / "result.json").read_text())
    registration = json.loads((folder / "registration.json").read_text())
    for path, digest in registration["sources"].items():
        if sha(ROOT / path) != digest:
            raise ValueError("Registered native source changed")
    if sha(folder / "config.json") != registration["config_sha256"]:
        raise ValueError("Native config changed")
    if result["status"] != "NATIVE_BACKTEST_COMPLETE":
        raise ValueError("Native backtest incomplete")
    expected = pd.date_range(START, END - pd.Timedelta(minutes=5), freq="5min")
    rows = result["native_execution_rows"]
    if set(rows) != set(contract["pairs"]):
        raise ValueError("Native pair coverage incomplete")
    if any(
        r["rows"] != len(expected)
        or pd.Timestamp(r["first"]) != expected[0]
        or pd.Timestamp(r["last"]) != expected[-1]
        for r in rows.values()
    ):
        raise ValueError("Native execution window incomplete")
    coverage = result["signal_coverage"]
    if {c["pair"] for c in coverage} != set(contract["pairs"]):
        raise ValueError("Native signal pair coverage incomplete")
    if any(
        c["signal_rows"] != len(expected)
        or any(
            not v["present"] or v["finite_rows"] != len(expected)
            for v in c["core_indicators"].values()
        )
        for c in coverage
    ):
        raise ValueError("Signal rows or inspected indicators incomplete")
    archive = result["native_result"]
    with zipfile.ZipFile(archive) as bundle:
        stats = next(
            json.loads(bundle.read(n))["strategy"]["WindowReplay"]
            for n in bundle.namelist()
            if n.endswith(".json") and not n.endswith("_config.json")
        )
        wallet_name = next(n for n in bundle.namelist() if n.endswith("_wallet.feather"))
        wallet = pd.read_feather(io.BytesIO(bundle.read(wallet_name)))
    if list(wallet.date) != list(expected):
        raise ValueError("Native wallet execution clocks incomplete")
    if stats["total_trades"] != result["native_trade_count"]:
        raise ValueError("Native archive/result trade mismatch")
    trades = [
        {
            k: t[k]
            for k in (
                "pair",
                "is_short",
                "open_date",
                "close_date",
                "open_rate",
                "close_rate",
                "amount",
                "stake_amount",
                "leverage",
                "enter_tag",
                "exit_reason",
                "profit_abs",
            )
        }
        for t in stats["trades"]
    ]
    return {
        "raw_entry_signals": len(result["raw_entry_signals"]),
        "signal_events": result["raw_entry_signals"],
        "entry_attempts": len(result["native_entry_attempts"]),
        "entry_confirmations": result["entry_confirmations"],
        "trades": trades,
        "trade_count": stats["total_trades"],
        "native_net_return_pct": stats["profit_total"] * 100,
        "native_execution_clocks_per_pair": len(expected),
        "inspected_indicators_nonmissing": True,
        "archive": {"path": archive, "sha256": sha(archive)},
        "registration": {
            "path": str(folder / "registration.json"),
            "sha256": sha(folder / "registration.json"),
        },
        "result": {"path": str(folder / "result.json"), "sha256": sha(folder / "result.json")},
    }


def live_evidence(market, contract):
    folder = ROOT / "user_data/deployment/mainnet-100-20261109" / market
    if sha(folder / "config.json") != contract["live_config_sha256"]:
        raise ValueError("Live config changed during comparison")
    with closing(sqlite3.connect((folder / "trades.sqlite").as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        trades = [
            dict(r)
            for r in db.execute(
                "SELECT id,pair,is_short,is_open,open_date,close_date,open_rate,amount,"
                "stake_amount,leverage,enter_tag FROM trades"
            )
        ]
        orders = [
            dict(r)
            for r in db.execute(
                "SELECT id,ft_trade_id,ft_pair,ft_order_side,order_type,status,amount,filled,"
                "average,order_date,order_filled_date FROM orders"
            )
        ]
    trades = [t for t in trades if START <= pd.Timestamp(t["open_date"], tz="UTC") < END]
    orders = [o for o in orders if START <= pd.Timestamp(o["order_date"], tz="UTC") < END]
    pid = json.loads((folder / "worker.pid").read_text())["pid"]
    return {
        "window_trades": trades,
        "window_orders": orders,
        "window_trade_count": len(trades),
        "window_order_count": len(orders),
        "window_filled_order_count": sum(float(o["filled"] or 0) > 0 for o in orders),
        "pid": pid,
        "process_alive": psutil.pid_exists(pid),
        "config_unchanged": True,
        "source": "GET-free read-only native SQLite ledger, not independent broker-fill audit",
    }


def main():
    plan = json.loads((OUT / "plan.json").read_text())
    for path, digest in plan["orchestration_sources"].items():
        if sha(ROOT / path) != digest:
            raise ValueError("Frozen workflow changed")
    accounts = {}
    for market, contract in plan["accounts"].items():
        accounts[market] = {
            "pairs": len(contract["pairs"]),
            "initial_usdt": 100,
            "stake_budget_usdt": 99,
            "max_open_trades": contract["max_open_trades"],
            "leverage": contract["leverage"],
            "native": native_evidence(market, contract),
            "live": live_evidence(market, contract),
        }
    output = {
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "start_utc": str(START),
        "end_exclusive_utc": str(END),
        "start_shanghai": str(START.tz_convert("Asia/Shanghai")),
        "end_exclusive_shanghai": str(END.tz_convert("Asia/Shanghai")),
        "primary_engine": "freqtrade.optimize.backtesting.Backtesting.start",
        "accounts": accounts,
        "limits": [
            "5m public OHLCV replay cannot reproduce intrabar execution time or order-book price",
            "Modeled13bps spot/8bps futures per side are not certified live commission",
            "Native force_exit at the window end is hypothetical, not a live exit",
            "Live ledger/logs do not replace an authenticated broker fill reconciliation",
            "This short window cannot establish profitability, drawdown robustness or win rate",
        ],
        "live_restarts": 0,
        "orders_forced": 0,
        "paper_workers_started": 0,
    }
    save(HERE / "comparison.json", output)
    print(json.dumps(output, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
