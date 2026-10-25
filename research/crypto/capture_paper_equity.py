"""Read-only paper NAV snapshots with native partial-fill/funding accounting and fresh marks."""

import argparse
import fcntl
import json
import os
import sqlite3
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from freqtrade.enums import TradingMode
from freqtrade.persistence import LocalTrade
from freqtrade.research.data import read, save, sha
from research.crypto.run_nfi import open_trade_value


def records(database):
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        return {
            table: [dict(row) for row in connection.execute("select * from " + table)]
            for table in ("trades", "orders", "trade_custom_data")
        }


def marked_campaign(row, orders, mark):
    fields = (
        "id",
        "pair",
        "is_short",
        "amount",
        "open_rate",
        "fee_open",
        "fee_close",
        "stake_amount",
        "max_stake_amount",
        "funding_fees",
        "realized_profit",
        "leverage",
    )
    trade = LocalTrade(**{key: row[key] for key in fields})
    trade.trading_mode = TradingMode(row["trading_mode"].lower())
    trade.orders = [SimpleNamespace(**item) for item in orders]
    return open_trade_value(trade, mark)


def check_marks(marks, symbols, now_ms):
    used = {}
    for symbol in symbols:
        row = next((x for x in marks if x["symbol"] == symbol), None)
        if row is None or not 0 < float(row["markPrice"]) < float("inf"):
            raise ValueError(f"Missing or invalid mark for {symbol}")
        age = now_ms - int(row["time"])
        if not -5000 <= age <= 120000:
            raise ValueError(f"Stale or future mark for {symbol}: age={age}ms")
        used[symbol] = row
    if (
        used
        and max(int(x["time"]) for x in used.values()) - min(int(x["time"]) for x in used.values())
        > 5000
    ):
        raise ValueError("Mark snapshots are not synchronous within5seconds")
    return used


def public_marks(symbols):
    marks = []
    for symbol in sorted(symbols):
        url = "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=" + symbol
        with urllib.request.urlopen(url, timeout=15) as response:
            marks.append(json.load(response))
    return marks


def capture(package, output, marks_path=None):
    if output.exists():
        raise ValueError("Preserve previous evidence snapshots")
    manifest = read(package / "package.json")
    if manifest["real_orders_enabled"] or manifest["mode"] != "paper_fixed_rule_control":
        raise ValueError("Expected an isolated paper portfolio")
    accounts, symbols = [], set()
    for component in manifest["components"]:
        folder = (package / component["directory"]).resolve()
        if not folder.is_relative_to(package):
            raise ValueError("Component escaped its package")
        config_path = folder / "settings.json"
        config = read(config_path)
        if sha(config_path) != component["settings_sha256"] or config["dry_run"] is not True:
            raise ValueError("Paper configuration drifted")
        if config["db_url"] != "sqlite:///" + str(folder / "paper.sqlite"):
            raise ValueError("Expected the component-owned database")
        ledger = records(folder / "paper.sqlite")
        for trade in ledger["trades"]:
            if trade["is_open"]:
                symbols.add(trade["pair"].split("/")[0] + "USDT")
        accounts.append((component, config, ledger))
    if marks_path:
        marks, source = read(marks_path)["marks"], str(marks_path)
    else:
        marks = public_marks(symbols)
        source = "Binance public /fapi/v1/premiumIndex; no account API or credentials"
    now = datetime.now(UTC)
    used = check_marks(marks, symbols, int(now.timestamp() * 1000))
    output.mkdir(parents=True)
    components = []
    for component, config, ledger in accounts:
        closed = [x for x in ledger["trades"] if not x["is_open"]]
        opened = [x for x in ledger["trades"] if x["is_open"]]
        pnl = sum(float(x["close_profit_abs"]) for x in closed)
        valuations = []
        for trade in opened:
            symbol = trade["pair"].split("/")[0] + "USDT"
            orders = [x for x in ledger["orders"] if x["ft_trade_id"] == trade["id"]]
            value = marked_campaign(trade, orders, float(used[symbol]["markPrice"]))
            valuations.append(
                {
                    "trade_id": trade["id"],
                    "pair": trade["pair"],
                    "mark_price": float(used[symbol]["markPrice"]),
                    "mark_time_ms": used[symbol]["time"],
                    "net_open_campaign_pnl_usdt": value,
                    "native_recorded_funding_usdt": trade["funding_fees"],
                    "native_stop": trade["stop_loss"],
                }
            )
        open_pnl = sum(x["net_open_campaign_pnl_usdt"] for x in valuations)
        save(output / (component["directory"] + "_ledger.json"), ledger)
        components.append(
            {
                "component": component["directory"],
                "candidate": component["candidate"],
                "initial_equity_usdt": config["dry_run_wallet"],
                "closed_net_pnl_usdt": pnl,
                "net_open_campaign_pnl_usdt": open_pnl,
                "marked_equity_estimate_usdt": config["dry_run_wallet"] + pnl + open_pnl,
                "closed_campaigns": len(closed),
                "open_campaigns": len(opened),
                "valuations": valuations,
            }
        )
    save(output / "marks.json", {"source": source, "marks": marks})
    result = {
        "captured_utc": now.isoformat(),
        "package": str(package),
        "candidate": manifest["candidate"],
        "components": components,
        "marked_equity_estimate_usdt": sum(x["marked_equity_estimate_usdt"] for x in components),
        "source": source,
        "marks_sha256": sha(output / "marks.json"),
        "funding_scope": "Native accrued funding only; forecast rates are not added as cash",
        "missing_evidence": [
            "Funding events not independently reconciled against venue history",
            "Marks are a NAV proxy, not executable bid/ask liquidation proceeds",
            "Sparse snapshots do not establish intrahour drawdown or a daily Sharpe",
        ],
        "historical_control_not_risk_variant": "RiskCap" not in manifest["candidate"],
        "forward_profitability_qualified": False,
        "real_orders_enabled": False,
    }
    save(output / "snapshot.json", result)
    return result


def watch(package, output, interval):
    if interval < 30:
        raise ValueError("Read-only collection interval must be at least30seconds")
    output.mkdir(parents=True, exist_ok=True)
    lock = (output / ".observer.lock").open("a+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    identity = sha(Path(__file__))
    manifest_sha = sha(package / "package.json")
    save(
        output / "observer_identity.json",
        {
            "pid": os.getpid(),
            "package": str(package),
            "source_sha256": identity,
            "package_sha256": manifest_sha,
            "interval_seconds": interval,
            "writes_to_trading_databases": False,
            "real_orders_enabled": False,
        },
    )
    while True:
        if sha(Path(__file__)) != identity or sha(package / "package.json") != manifest_sha:
            raise ValueError("Observer source/account changed; explicit restart required")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        try:
            result = capture(package, output / stamp)
            save(
                output / "latest.json",
                {
                    "state": "CAPTURED",
                    "snapshot": str(output / stamp / "snapshot.json"),
                    "captured_utc": result["captured_utc"],
                    "pid": os.getpid(),
                    "marked_equity_estimate_usdt": result["marked_equity_estimate_usdt"],
                    "source_sha256": identity,
                },
            )
        except (OSError, ValueError, KeyError, sqlite3.Error) as error:
            failure = {
                "state": "ERROR_NO_NAV",
                "error": str(error),
                "pid": os.getpid(),
                "observed_utc": datetime.now(UTC).isoformat(),
            }
            save(output / (stamp + ".error.json"), failure)
            save(output / "latest.json", failure)
        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--marks", type=Path)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()
    if args.watch:
        if args.marks:
            raise ValueError("A continuous observer must fetch fresh public marks each cycle")
        watch(args.package.resolve(), args.output.resolve(), args.interval)
    else:
        result = capture(args.package.resolve(), args.output.resolve(), args.marks)
        print(json.dumps(result, ensure_ascii=False))
