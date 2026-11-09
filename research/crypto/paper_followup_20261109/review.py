"""Observe actual native fills with native Freqtrade PnL; never generate hypothetical trades."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np

from freqtrade.enums import TradingMode
from freqtrade.persistence import LocalTrade
from research.crypto.paper_followup_20261109.snapshot import HERE, RAW, ref, save


def date(value):
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def load(reference):
    path = Path(reference["path"])
    if ref(path)["sha256"] != reference["sha256"]:
        raise ValueError("Frozen native observation source changed")
    return json.loads(path.read_text())


def profit(trade, rate):
    return trade.calculate_profit(rate).profit_abs


def native_position(raw):
    return LocalTrade(
        **{
            **raw,
            "trading_mode": TradingMode(raw["trading_mode"].lower()),
            "open_date": date(raw["open_date"]),
        }
    )


def actual_orders(raw, orders):
    """Admit the observed one-entry/one-exit lifecycle; reject unsupported adjustments."""
    own = [o for o in orders if o["ft_trade_id"] == raw["id"] and o["filled"] and o["filled"] > 0]
    entry_side = "sell" if raw["is_short"] else "buy"
    entries = [o for o in own if o["ft_order_side"] == entry_side]
    exits = [o for o in own if o["ft_order_side"] != entry_side]
    if len(entries) != 1 or len(exits) != (0 if raw["is_open"] else 1):
        raise ValueError("Actual adjustments/partial exits need native order-lifecycle extension")
    entry = entries[0]
    if raw["leverage"] != 1 or abs(entry["filled"] - raw["amount"]) > 1e-7:
        raise ValueError("Observation contract requires the actual1x unadjusted native positions")
    if (entry["average"] or entry["price"]) != raw["open_rate"]:
        raise ValueError("Native entry fill and recorded basis differ")
    if exits:
        out = exits[0]
        if abs(out["filled"] - entry["filled"]) > 1e-7:
            raise ValueError("Native closing fill does not flatten the observed amount")
        if (out["average"] or out["price"]) != raw["close_rate"]:
            raise ValueError("Native exit fill and recorded closing rate differ")
        if date(out["order_filled_date"]) != date(raw["close_date"]):
            raise ValueError("Use the actual native exit fill time, not order submission")
    return entry, exits[0] if exits else None


def manual_profit(raw, rate, funding):
    """Independent signed notional and fee arithmetic to audit the native PnL call."""
    q, opening, closing = (Decimal(str(x)) for x in (raw["amount"], raw["open_rate"], rate))
    side = Decimal(-1 if raw["is_short"] else 1)
    return float(
        side * q * (closing - opening)
        - q * opening * Decimal(str(raw["fee_open"]))
        - q * closing * Decimal(str(raw["fee_close"]))
        + Decimal(str(funding))
    )


def summarize(nav, grid, begin, trades, orders):
    first = grid.index(begin)
    values = nav[first:]
    result = {
        "start_utc": begin.isoformat(),
        "end_utc": grid[-1].isoformat(),
        "begin_nav_usdt": float(values[0]),
        "end_nav_usdt": float(values[-1]),
        "net_usdt": float(values[-1] - values[0]),
        "net_return_pct": float(100 * (values[-1] / values[0] - 1)),
        "minute_drawdown_pct": float(100 * np.max(1 - values / np.maximum.accumulate(values))),
        "completed_trades": sum(
            bool(t["close_date"] and begin < date(t["close_date"]) <= grid[-1]) for t in trades
        ),
        "entry_fills": 0,
        "exit_fills": 0,
        "actual_addition_fills": 0,
    }
    for trade in trades:
        entry, exit_order = actual_orders(trade, orders)
        result["entry_fills"] += int(begin < date(entry["order_filled_date"]) <= grid[-1])
        result["exit_fills"] += int(
            bool(exit_order and begin < date(exit_order["order_filled_date"]) <= grid[-1])
        )
    return result


def main():  # noqa: C901 -- bounded reconciliation with explicit source/contract guards
    snapshot = json.loads((RAW / "snapshot.json").read_text())
    load(snapshot["native_strategy_and_release"])
    load_check = Path(snapshot["native_trade_model"]["path"])
    if ref(load_check) != snapshot["native_trade_model"]:
        raise ValueError("Pinned native trade implementation changed")
    start, end = date(snapshot["start"]), date(snapshot["end"])
    grid = [
        start + timedelta(minutes=i) for i in range(int((end - start).total_seconds() / 60) + 1)
    ]
    sources, marks, funds = {}, {}, {}
    for key, instrument in snapshot["instruments"].items():
        rows = []
        for page in sorted(RAW.glob(key + "_[0-9].json")):
            sources[page.name] = ref(page)
            rows += json.loads(page.read_text())["data"]
        expected = [int(t.timestamp() * 1000) - 60000 for t in grid]
        if [row[0] for row in rows] != expected or any(r[6] != r[0] + 59999 for r in rows):
            raise ValueError("Incomplete, duplicate or unaligned public minute observations")
        marks[key] = [float(row[4]) for row in rows]
        if not np.isfinite(marks[key]).all() or min(marks[key]) <= 0:
            raise ValueError("Invalid public observation marks")
        if instrument["market"] == "futures":
            path = RAW / ("funding_" + instrument["symbol"] + ".json")
            value = json.loads(path.read_text())
            sources[path.name] = ref(path)
            events = value["data"]
            if len(events) >= value["arguments"]["limit"]:
                raise ValueError("Funding pagination may be incomplete")
            if [e["fundingTime"] for e in events] != sorted({e["fundingTime"] for e in events}):
                raise ValueError("Funding events must be unique and chronological")
            if any(e["symbol"] != instrument["symbol"] for e in events):
                raise ValueError("Funding instrument mismatch")
            funds[key] = events
    results, max_error, checked_points = {}, 0.0, 0
    for key, account in snapshot["accounts"].items():
        config = load(account["config"])
        if config["dry_run"] is not True or config["dry_run_wallet"] != 1000:
            raise ValueError("Frozen actual account is not the admitted paper contract")
        ledger = load(account["ledger"])
        trades, orders = ledger["trades"], ledger["orders"]
        nav = np.full(len(grid), float(account["initial_usdt"]))
        manual_nav = nav.copy()
        trade_details = []
        for raw in trades:
            entry, exit_order = actual_orders(raw, orders)
            opened = date(entry["order_filled_date"])
            closed = date(exit_order["order_filled_date"]) if exit_order else None
            if opened > end or (closed and closed > end):
                raise ValueError("Ledger contains activity beyond the frozen cutoff")
            trade = native_position(raw)
            if closed:
                terminal = profit(trade, raw["close_rate"])
                if abs(terminal - raw["close_profit_abs"]) > 1e-7:
                    raise ValueError(
                        "Native final profit does not reproduce recorded closed profit"
                    )
                if (
                    abs(manual_profit(raw, raw["close_rate"], raw["funding_fees"]) - terminal)
                    > 1e-6
                ):
                    raise ValueError(
                        "Native final profit failed independent fee/funding arithmetic"
                    )
            if closed and closed < start:
                nav += raw["close_profit_abs"]
                manual_nav += raw["close_profit_abs"]
                continue
            symbol = raw["pair"].split(":")[0].replace("/", "")
            market_key = account["market"] + "_" + symbol
            events = funds.get(market_key, [])
            relevant = [
                e
                for e in events
                if int(opened.timestamp() * 1000) <= e["fundingTime"]
                and (closed is None or e["fundingTime"] <= int(closed.timestamp() * 1000))
            ]
            sign = 1 if raw["is_short"] else -1
            public_funding = sum(
                sign * raw["amount"] * float(e["fundingRate"]) * float(e["markPrice"])
                for e in relevant
            )
            for i, clock in enumerate(grid):
                if clock < opened:
                    continue
                if closed and clock >= closed:
                    nav[i] += raw["close_profit_abs"]
                    manual_nav[i] += manual_profit(raw, raw["close_rate"], raw["funding_fees"])
                    continue
                accrued = sum(
                    sign * raw["amount"] * float(e["fundingRate"]) * float(e["markPrice"])
                    for e in relevant
                    if e["fundingTime"] <= int(clock.timestamp() * 1000)
                )
                trade.funding_fees = accrued
                mark = marks[market_key][i]
                nav[i] += profit(trade, mark)
                manual_nav[i] += manual_profit(raw, mark, accrued)
            trade_details.append(
                {
                    "id": raw["id"],
                    "pair": raw["pair"],
                    "is_short": bool(raw["is_short"]),
                    "actual_entry_fill_utc": opened.isoformat(),
                    "actual_exit_fill_utc": closed.isoformat() if closed else None,
                    "open": bool(raw["is_open"]),
                    "native_realized_usdt": raw["close_profit_abs"],
                    "entry_tag": raw["enter_tag"].strip(),
                    "actual_additions": 0,
                    "native_stored_funding_usdt": raw["funding_fees"],
                    "public_lifetime_funding_usdt": public_funding,
                    "public_minus_native_funding_usdt": public_funding - raw["funding_fees"],
                }
            )
        if not np.isfinite(nav).all() or min(nav) <= 0:
            raise ValueError("Invalid observed native account NAV")
        error = float(max(abs(nav - manual_nav)))
        if error > 1e-6:
            raise ValueError("Native PnL and independent ledger arithmetic disagree")
        max_error, checked_points = max(max_error, error), checked_points + len(grid)
        target = RAW / (key + "_nav.json")
        save(target, {"clock_utc": [t.isoformat() for t in grid], "native_pnl_nav_usdt": list(nav)})
        realized = sum(t["close_profit_abs"] for t in trades if not t["is_open"])
        results[key] = {
            "full_common_window": summarize(nav, grid, start, trades, orders),
            "post_repair_window": summarize(
                nav, grid, date(snapshot["post_repair_start"]), trades, orders
            ),
            "lifetime_realized_usdt": realized,
            "lifetime_net_return_pct": float(100 * (nav[-1] / 1000 - 1)),
            "open_positions": sum(t["is_open"] for t in trades),
            "observed_trades": trade_details,
            "ledger": account["ledger"],
            "nav": ref(target),
            "independent_max_nav_error_usdt": error,
        }
        print(
            key,
            "full",
            results[key]["full_common_window"],
            "post",
            results[key]["post_repair_window"],
            flush=True,
        )
    save(
        HERE / "result.json",
        {
            "scope": (
                "Operational follow-up of actual Freqtrade paper ledgers. Uses native LocalTrade "
                "PnL and native recorded closed profits; not a new backtest or an independent "
                "trading simulator."
            ),
            "snapshot": ref(RAW / "snapshot.json"),
            "native_trade_model": snapshot["native_trade_model"],
            "accounts": results,
            "public_sources": sources,
            "native_profit_nav_points_checked": checked_points,
            "maximum_independent_nav_error_usdt": max_error,
            "minute_observations_per_account": len(grid),
            "actual_additions": 0,
            "denied_addition_proposals": None,
            "cap_decision_telemetry_available": False,
            "optimized_improvement_verified": False,
            "selected_paper_baseline": "NostalgiaForInfinityX8 original",
            "limitations": [
                (
                    "Original and AddCap start with different positions and exposure; "
                    "same window does not identify a causal strategy effect."
                ),
                (
                    "Two closed AddCap futures positions, no spot trades and no actual additions "
                    "are too few to validate optimization."
                ),
                (
                    "No recorded callback proposal/denial trace: zero actual additions does not "
                    "prove the cap never rejected a proposal."
                ),
                (
                    "The full window includes pre-repair connectivity failures; the post-repair "
                    "slice does not certify continuous computer/network uptime."
                ),
                (
                    "Minute closes omit intraminute extremes; marked open profits include "
                    "estimated exit fees and public funding, not authenticated fills/settlement."
                ),
            ],
            "orders_enabled": False,
            "production_eligible": False,
            "implementation": {
                name: ref(HERE / name) for name in ("snapshot.py", "review.py", "test_review.py")
            },
        },
    )


if __name__ == "__main__":
    main()
