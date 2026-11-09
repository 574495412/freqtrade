"""Native-ledger cumulative and daily paper NAV, including actual open positions."""

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from research.crypto.paper_followup_20261109.review import (
    actual_orders,
    date,
    manual_profit,
    native_position,
    profit,
)
from research.crypto.paper_followup_20261109.snapshot import ref, save
from research.crypto.runtime_backtest_parity_20261109.parity import HERE, RAW, ROOT, load_checked


def public_inputs():
    prior = ROOT / "research/crypto/paper_followup_20261109/result.json"
    old = json.loads(prior.read_text())
    references = list(old["public_sources"].values())
    references += [
        ref(p) for p in sorted(RAW.glob("*.json")) if "data" in json.loads(p.read_text())
    ]
    prices, funding = {}, {}
    for reference in references:
        source = load_checked(reference)
        args, rows = source["arguments"], source["data"]
        symbol = args["symbol"]
        if "funding_rate_history" in source["tool"]:
            book = funding.setdefault(symbol, {})
            if len(rows) >= args["limit"]:
                raise ValueError("Funding response may need pagination")
            for row in rows:
                if row["symbol"] != symbol:
                    raise ValueError("Incorrect funding instrument")
                book[row["fundingTime"]] = row
        else:
            market = "spot" if "_spot_" in source["tool"] else "futures"
            for row in rows:
                if row[6] != row[0] + 59999:
                    raise ValueError("Invalid completed minute clock")
                when = datetime.fromtimestamp((row[0] + 60000) / 1000, UTC)
                prices[(market, symbol, when)] = float(row[4])
    return prices, funding, references


def mark_account(account, ledger, at, prices, funding):
    nav, independent = 1000.0, 1000.0
    details = []
    for raw in ledger["trades"]:
        entry, exit_order = actual_orders(raw, ledger["orders"])
        opened = date(entry["order_filled_date"])
        closed = date(exit_order["order_filled_date"]) if exit_order else None
        if at < opened:
            continue
        trade = native_position(raw)
        if closed and closed <= at:
            value = profit(trade, raw["close_rate"])
            if abs(value - raw["close_profit_abs"]) > 1e-7:
                raise ValueError("Native closed profit and recorded ledger differ")
            nav += raw["close_profit_abs"]
            independent += manual_profit(raw, raw["close_rate"], raw["funding_fees"])
            continue
        symbol = raw["pair"].split(":")[0].replace("/", "")
        mark = prices[(account["market"], symbol, at)]
        if mark <= 0:
            raise ValueError("Invalid observed open-position mark")
        accrued = 0.0
        if account["market"] == "futures":
            accrued = sum(
                (1 if raw["is_short"] else -1)
                * raw["amount"]
                * float(event["fundingRate"])
                * float(event["markPrice"])
                for stamp, event in funding[symbol].items()
                if int(opened.timestamp() * 1000) <= stamp <= int(at.timestamp() * 1000)
            )
        trade.funding_fees = accrued
        value = profit(trade, mark)
        nav += value
        independent += manual_profit(raw, mark, accrued)
        details.append(
            {
                "pair": raw["pair"],
                "amount": raw["amount"],
                "is_short": bool(raw["is_short"]),
                "entry_fill_utc": opened.isoformat(),
                "mark": mark,
                "native_mark_profit_usdt": value,
                "public_accrued_funding_usdt": accrued,
                "stored_native_funding_usdt": raw["funding_fees"],
                "funding_difference_usdt": accrued - raw["funding_fees"],
            }
        )
    if abs(nav - independent) > 1e-6:
        raise ValueError("Native NAV failed independent signed-fill/fee/funding audit")
    return nav, abs(nav - independent), details


def main():
    snapshot = json.loads((RAW / "snapshot.json").read_text())
    end = date(snapshot["end_utc"])
    zone = ZoneInfo("Asia/Shanghai")
    prices, funding, sources = public_inputs()
    accounts, max_error, maximum_delay = {}, 0.0, 0.0
    for key, account in snapshot["accounts"].items():
        ledger = load_checked(account["ledger"])
        config = load_checked(account["config"])
        if config["dry_run"] is not True or config["dry_run_wallet"] != 1000:
            raise ValueError("Only the existing simulated1000USDT accounts are supported")
        started = date(
            next(
                r["datetime_value"] for r in ledger["account_times"] if r["key"] == "bot_start_time"
            )
        )
        nav, error, positions = mark_account(account, ledger, end, prices, funding)
        max_error = max(max_error, error)
        realized = sum(t["close_profit_abs"] for t in ledger["trades"] if not t["is_open"])
        daily = []
        day = started.astimezone(zone).date()
        while day <= end.astimezone(zone).date():
            opening = max(started, datetime.combine(day, datetime.min.time(), zone).astimezone(UTC))
            closing = min(
                end,
                datetime.combine(day + timedelta(days=1), datetime.min.time(), zone).astimezone(
                    UTC
                ),
            )
            first, err1, _ = mark_account(account, ledger, opening, prices, funding)
            last, err2, _ = mark_account(account, ledger, closing, prices, funding)
            max_error = max(max_error, err1, err2)
            daily.append(
                {
                    "beijing_date": day.isoformat(),
                    "start_utc": opening.isoformat(),
                    "end_utc": closing.isoformat(),
                    "pnl_usdt": last - first,
                    "net_return_pct": 100 * (last / first - 1),
                }
            )
            day += timedelta(days=1)
        if abs(sum(r["pnl_usdt"] for r in daily) - (nav - 1000)) > 1e-7:
            raise ValueError("Daily native marked PnL does not telescope to lifetime account PnL")
        for order in ledger["orders"]:
            if order["filled"] and order["filled"] > 0:
                delay = (
                    date(order["order_filled_date"]) - date(order["order_date"])
                ).total_seconds()
                maximum_delay = max(maximum_delay, delay)
        accounts[key] = {
            "account_start_beijing": started.astimezone(zone).isoformat(),
            "initial_usdt": 1000,
            "end_nav_usdt": nav,
            "realized_profit_usdt": realized,
            "open_mark_profit_usdt": sum(p["native_mark_profit_usdt"] for p in positions),
            "net_profit_usdt": nav - 1000,
            "net_return_pct": 100 * (nav / 1000 - 1),
            "closed_trades": sum(not t["is_open"] for t in ledger["trades"]),
            "open_positions": positions,
            "actual_additions": 0,
            "daily": daily,
            "ledger": account["ledger"],
            "config": account["config"],
        }
        print(
            key,
            "net_USDT",
            round(nav - 1000, 6),
            "return%",
            round(100 * (nav / 1000 - 1), 6),
            "realized",
            round(realized, 6),
            flush=True,
        )
    save(
        HERE / "returns.json",
        {
            "scope": "Actual local Freqtrade paper books, not real exchange account returns.",
            "as_of_beijing": end.astimezone(zone).isoformat(),
            "snapshot": ref(RAW / "snapshot.json"),
            "accounts": accounts,
            "public_sources": sources,
            "maximum_independent_native_nav_error_usdt": max_error,
            "maximum_observed_filled_order_delay_seconds": maximum_delay,
            "valuation": (
                "Native recorded closed profits plus native LocalTrade profit for actual open "
                "positions at completed minute closes/marks, with entry and estimated exit fees "
                "and actual public funding. Native closed funding remains the booked amount."
            ),
            "limitations": [
                "Original beganNov5; AddCap beganNov7. Lifetime returns are not matched cohorts.",
                "Heartbeat gaps and unresolved lookahead diagnostics remain qualification gaps.",
                "Marked values and estimated fees are not executable liquidation proceeds.",
            ],
            "observer_submits_orders": False,
            "real_orders_enabled": False,
            "new_backtests": 0,
            "strategy_configuration_changed": False,
            "implementation": {name: ref(HERE / name) for name in ("parity.py", "returns.py")},
            "native_trade_model": ref(ROOT / "freqtrade/persistence/trade_model.py"),
        },
    )


if __name__ == "__main__":
    main()
