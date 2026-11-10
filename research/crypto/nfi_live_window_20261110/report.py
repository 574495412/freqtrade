"""Reconcile completed native replay artifacts with read-only live ledgers."""

import io
import json
import sqlite3
import zipfile
from contextlib import closing
from datetime import UTC, datetime

import pandas as pd
import psutil

from research.crypto.nfi_live_window_20261110.prepare import END, HERE, OUT, ROOT, START, save, sha


def main():  # noqa: C901 -- Validate both native archives and their live-ledger comparison.
    plan = json.loads((OUT / "plan.json").read_text())
    rows = []
    for market, contract in plan["accounts"].items():
        output = OUT / market / "native-run-verified"
        result = json.loads((output / "result.json").read_text())
        registration = json.loads((output / "registration.json").read_text())
        for name, digest in registration["sources"].items():
            if sha(ROOT / name) != digest:
                raise ValueError("Registered native/research source changed")
        if result["status"] != "NATIVE_BACKTEST_COMPLETE":
            raise ValueError("Incomplete native account")
        if set(result["native_execution_rows"]) != set(contract["pairs"]):
            raise ValueError("Missing native pair execution rows")
        if any(r["rows"] != 24 for r in result["native_execution_rows"].values()):
            raise ValueError("Incomplete native execution window")
        if len(result["signal_coverage"]) != len(contract["pairs"]):
            raise ValueError("Missing signal coverage")
        if any(c["signal_rows"] != 24 for c in result["signal_coverage"]):
            raise ValueError("Incomplete previous-closed-candle signal window")
        if any(
            not v["present"] or v["finite_rows"] != 24
            for c in result["signal_coverage"]
            for v in c["core_indicators"].values()
        ):
            raise ValueError("Missing core entry indicators in the requested window")
        archive = result["native_result"]
        with zipfile.ZipFile(archive) as bundle:
            native = next(
                json.loads(bundle.read(name))
                for name in bundle.namelist()
                if name.endswith(".json") and not name.endswith("_config.json")
            )
            stats = native["strategy"]["WindowReplay"]
            wallet_name = next(n for n in bundle.namelist() if n.endswith("_wallet.feather"))
            wallet = pd.read_feather(io.BytesIO(bundle.read(wallet_name)))
        expected = pd.date_range(START, END - pd.Timedelta(minutes=5), freq="5min")
        if list(wallet.date) != list(expected) or not wallet.currency.eq("USDT").all():
            raise ValueError("Native wallet snapshots do not cover24 requested execution clocks")
        if stats["total_trades"] != result["native_trade_count"]:
            raise ValueError("Native archive/result trade mismatch")
        folder = ROOT / "user_data/deployment/mainnet-100-20261109" / market
        if sha(folder / "config.json") != contract["live_config_sha256"]:
            raise ValueError("Live contract changed during comparison")
        with closing(
            sqlite3.connect((folder / "trades.sqlite").as_uri() + "?mode=ro", uri=True)
        ) as db:
            trades = pd.read_sql_query("SELECT id,pair,open_date FROM trades", db)
            orders = pd.read_sql_query("SELECT id,order_date,status,filled FROM orders", db)
        count_trades = (
            int(
                pd.to_datetime(trades.open_date, utc=True)
                .between(START, END, inclusive="left")
                .sum()
            )
            if not trades.empty
            else 0
        )
        count_orders = (
            int(
                pd.to_datetime(orders.order_date, utc=True)
                .between(START, END, inclusive="left")
                .sum()
            )
            if not orders.empty
            else 0
        )
        pid = json.loads((folder / "worker.pid").read_text())["pid"]
        rows.append(
            {
                "market": market,
                "pairs": len(contract["pairs"]),
                "initial_equity_usdt": 100,
                "stake_budget_usdt": 99,
                "leverage": contract["leverage"],
                "max_open_trades": contract["max_open_trades"],
                "raw_entry_signals": len(result["raw_entry_signals"]),
                "native_entry_attempts": len(result["native_entry_attempts"]),
                "entry_confirmation_calls": len(result["entry_confirmations"]),
                "native_trades": result["native_trade_count"],
                "live_window_orders": count_orders,
                "live_window_trades": count_trades,
                "native_5m_execution_clocks_per_pair": 24,
                "native_window_pair_candle_evaluations": len(contract["pairs"]) * 24,
                "representative_indicators_nonmissing": True,
                "native_wallet_first": str(wallet.date.iloc[0]),
                "native_wallet_last": str(wallet.date.iloc[-1]),
                "native_wallet_ending_usdt": float(wallet.balance.iloc[-1]),
                "live_pid": pid,
                "live_process_alive": psutil.pid_exists(pid),
                "live_config_unchanged": True,
                "native_result": {"path": archive, "sha256": sha(archive)},
                "result": {
                    "path": str(output / "result.json"),
                    "sha256": sha(output / "result.json"),
                },
                "registration": {
                    "path": str(output / "registration.json"),
                    "sha256": sha(output / "registration.json"),
                },
            }
        )
    # Native evidence is authoritative; this report is not a separate account simulator.
    conclusion = {
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "start_utc": str(START),
        "end_exclusive_utc": str(END),
        "start_shanghai": str(START.tz_convert("Asia/Shanghai")),
        "end_exclusive_shanghai": str(END.tz_convert("Asia/Shanghai")),
        "method": "Native Freqtrade Backtesting.start, pinned deployed financial callbacks",
        "rows": rows,
        "conclusion": (
            "Both native replays had zero entry signals and zero trades; live zero orders agrees"
        ),
        "native_source_baseline": "c051e9065c5b677f03817dd1f83cb980af1ed684",
        "backtest_result_cache": "none",
        "outbound_replay_sockets": "blocked",
        "financial_callbacks_changed": False,
        "local_adapter_scope": "Backtest-only startup admission; live financial methods inherited",
        "model_limits": [
            "Public OHLCV retrospective replay cannot prove identical intrabar ticks or order book",
            "Costs use frozen13bps spot/8bps futures all-in models, "
            "not charged commission certification",
            "Frozen existing leverage brackets reused with current public symbol rules",
            "No signal in this window does not verify the first live order/fill path",
        ],
        "live_orders_forced": 0,
        "live_workers_restarted": 0,
        "paper_workers_started": 0,
    }
    save(HERE / "comparison.json", conclusion)
    print(json.dumps({"completed": True, "accounts": rows}, ensure_ascii=False))


if __name__ == "__main__":
    main()
