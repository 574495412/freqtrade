"""Finite A05 launch cohorts in a shared, fully collateralized short account."""

import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.listing_reversal_20261107.catalog import HERE, OUT, now
from research.crypto.listing_reversal_20261107.intake import ADMISSION


PLAN = HERE / "registration.json"
CASE_NAMES = ("FailedLaunch", "AllLaunches")
SPLITS = ("2025-01-01", "2026-01-01", "2026-09-06")
FIVE_MINUTES_MS = 300_000
HOUR = 12
DAY = 24 * HOUR


@dataclass
class Market:
    name: str
    launch: int
    close: np.ndarray
    quote: np.ndarray
    mark_close: np.ndarray
    mark_high: np.ndarray
    funding: dict
    pump: float
    pullback: float
    day_quote: float

    def index(self, clock):
        i = clock - self.launch - 1
        if not 0 <= i < len(self.close):
            raise ValueError(f"Open exposure without sealed market interval: {self.name}/{clock}")
        return i


@dataclass
class Position:
    qty: float
    entry: float
    expiry: int
    opened: int
    net_cash: float
    pending_since: int | None = None


def register():
    if PLAN.exists():
        raise FileExistsError("Economic protocol already frozen")
    save(
        PLAN,
        {
            "registered_utc": now(),
            "admission_registration_sha256": sha(ADMISSION),
            "replay_sha256": sha(Path(__file__)),
            "hypothesis": (
                "After a 24h listing pump fails, the next 72h of downside can be captured with "
                "better net economics than shorting all liquid launches."
            ),
            "cases": CASE_NAMES,
            "first_launch_day": "2025-01-01",
            "last_launch_day_exclusive": "2026-09-01",
            "common_start": SPLITS[0],
            "split": SPLITS[1],
            "common_end": SPLITS[2],
            "day_quote_minimum": 5_000_000,
            "minimum_pump": 0.20,
            "minimum_pullback": 0.15,
            "signal": (
                "At launch plus 24h require 5m quote turnover >= 5m USDT. FailedLaunch also "
                "requires a 24h high at least 20% above the first completed 5m close and latest "
                "close at least 15% below that high. No economic parameter sweep."
            ),
            "capital": 1000,
            "maximum_slots": 3,
            "stake_fraction": 0.25,
            "margin_fraction": 0.75,
            "leverage": 1,
            "fees_bps": 5,
            "cost_bps": [15, 40],
            "volume_participation": 0.01,
            "hold_hours": 72,
            "stop_mark_increase": 0.25,
            "margin_loss_mark_increase": 0.90,
            "maximum_observed_drawdown": 0.40,
            "execution": (
                "Short the next completed 5m close with adverse price adjustment and fees. "
                "Cap order against available wallet, outstanding entry collateral, prior hour "
                "quote and actual next interval quote. Entries expire after one bar. Earlier "
                "exit requests fill ahead of new entries subject to volume, persisting if partial. "
                "A 25% adverse completed mark high requests exit on the next 5m close. At a 90% "
                "adverse mark high while held, book a full loss of remaining committed collateral "
                "as a catastrophe convention. This is not an exchange liquidation calculation. "
                "Settle exact public funding before boundary fills while the position is open. "
                "Deduct estimated exit friction from NAV. A 40% observed account drawdown stops "
                "new entries and requests outstanding exits."
            ),
            "decision": (
                "Both costs positive over full and both continuous time slices, beat AllLaunches "
                "full-window net return, at least 30 closes across at least 12 UTC entry weeks, "
                "no catastrophic loss, no account halt and max drawdown <=40%. Separately report "
                "25% stress CAGR hurdle. Keep unknown inputs visible and qualify scope."
            ),
            "orders_enabled": False,
        },
    )


def bar_number(value):
    time = pd.Timestamp(value)
    return time.value // (FIVE_MINUTES_MS * 1_000_000)


def iso(clock):
    return pd.Timestamp(clock * FIVE_MINUTES_MS, unit="ms", tz="UTC").isoformat()


def market_data(plan, intake):
    if intake["registration_sha256"] != plan["admission_registration_sha256"]:
        raise ValueError("Unregistered data")
    for row in intake["rows"]:
        if row["state"] != "ADMITTED":
            continue
        name = row["symbol"]
        tables = {}
        for kind in ("trade", "mark", "funding"):
            path = OUT / "data" / name / f"{kind}.parquet"
            if sha(path) != row[f"{kind}_sha256"]:
                raise ValueError(f"Sealed data changed: {path}")
            tables[kind] = pd.read_parquet(path)
        trade, mark, funding = (tables[k] for k in ("trade", "mark", "funding"))
        events = defaultdict(list)
        for actual, value in funding.iterrows():
            clock = bar_number(actual.ceil("5min"))
            events[clock].append((actual.isoformat(), value.rate, value.mark))
        high = trade.high.iloc[:DAY].max()
        yield Market(
            name,
            bar_number(row["launch_utc"]),
            trade.close.to_numpy(),
            trade.quote_volume.to_numpy(),
            mark.close.to_numpy(),
            mark.high.to_numpy(),
            events,
            float(high / trade.close.iloc[0] - 1),
            float(1 - trade.close.iloc[DAY - 1] / high),
            float(trade.quote_volume.iloc[:DAY].sum()),
        )


class Account:
    def __init__(self, plan, cost):
        self.plan = plan
        self.fee = plan["fees_bps"] / 10000
        self.slip = (cost - plan["fees_bps"]) / 10000
        self.cash = self.peak = float(plan["capital"])
        self.halted = False
        self.hold = {}
        self.events = []
        self.finished = []
        self.nav = []

    def book(self, clock, symbol, kind, qty, rate, cash_delta, **fields):
        self.cash += cash_delta
        self.events.append(
            {
                "clock": clock,
                "symbol": symbol,
                "kind": kind,
                "qty": qty,
                "rate": rate,
                "cash_delta": cash_delta,
                **fields,
            }
        )

    def value(self, clock, markets):
        nav = self.cash
        for symbol, position in self.hold.items():
            market = markets[symbol]
            mark = market.mark_close[market.index(clock)]
            nav += position.qty * (position.entry - mark * (1 + self.slip) * (1 + self.fee))
        return nav

    def positions(self, clock, markets):
        for symbol, pos in list(self.hold.items()):
            market = markets[symbol]
            i = market.index(clock)
            for actual, rate, mark in market.funding.get(clock, ()):
                if pd.Timestamp(actual) > pd.Timestamp(iso(pos.opened)):
                    delta = pos.qty * rate * mark
                    self.book(
                        clock, symbol, "funding", pos.qty, mark, delta, actual=actual, funding=rate
                    )
                    pos.net_cash += delta
            catastrophic = market.mark_high[i] >= pos.entry * (
                1 + self.plan["margin_loss_mark_increase"]
            )
            if catastrophic:
                delta = -pos.qty * pos.entry
                self.book(clock, symbol, "collateral_loss", pos.qty, pos.entry, delta)
                self.close(symbol, pos, clock, delta)
                continue
            expired = clock > pos.expiry
            if expired or (pos.pending_since is not None and clock > pos.pending_since):
                price = market.close[i] * (1 + self.slip)
                cap = market.quote[i] * self.plan["volume_participation"] / price
                qty = min(cap, pos.qty)
                if qty > 0:
                    delta = qty * (pos.entry - price * (1 + self.fee))
                    self.book(clock, symbol, "exit", qty, price, delta)
                    pos.qty -= qty
                    if pos.qty <= 1e-10:
                        self.close(symbol, pos, clock, delta)
                        continue
                    pos.net_cash += delta
            adverse = market.mark_high[i] >= pos.entry * (1 + self.plan["stop_mark_increase"])
            if adverse and pos.pending_since is None:
                pos.pending_since = clock

    def close(self, symbol, pos, clock, delta):
        self.finished.append(
            {
                "symbol": symbol,
                "entry": iso(pos.opened),
                "exit": iso(clock),
                "net_usdt": pos.net_cash + delta,
            }
        )
        del self.hold[symbol]

    def enter(self, clock, market, nav):
        used = math.fsum(p.qty * p.entry for p in self.hold.values())
        budget = min(
            self.plan["stake_fraction"] * nav,
            self.plan["margin_fraction"] * nav - used,
            (self.cash - used) / (1 + self.fee),
        )
        i = market.index(clock)
        price = market.close[i] * (1 - self.slip)
        cap = self.plan["volume_participation"] * min(
            market.quote[i], market.quote[i - HOUR : i].mean()
        )
        notional = min(budget, cap)
        if notional > 0:
            qty, fee = notional / price, notional * self.fee
            self.book(clock, market.name, "entry", qty, price, -fee)
            self.hold[market.name] = Position(
                qty,
                price,
                clock + self.plan["hold_hours"] * HOUR,
                clock,
                -fee,
            )

    def step(self, clock, markets, signals):
        self.positions(clock, markets)
        nav = self.value(clock, markets)
        self.peak = max(self.peak, nav)
        if nav < self.peak * (1 - self.plan["maximum_observed_drawdown"]):
            self.halted = True
            for pos in self.hold.values():
                if pos.pending_since is None:
                    pos.pending_since = clock
        for market in signals:
            if self.halted or len(self.hold) >= self.plan["maximum_slots"]:
                break
            self.enter(clock, market, self.value(clock, markets))
        nav = self.value(clock, markets)
        self.peak = max(self.peak, nav)
        self.nav.append((clock, self.cash, nav, len(self.hold)))


def simulate(plan, market, case, cost):
    account = Account(plan, cost)
    signals = defaultdict(list)
    for item in market.values():
        failed = item.pump >= plan["minimum_pump"] and item.pullback >= plan["minimum_pullback"]
        if item.day_quote >= plan["day_quote_minimum"] and (case == "AllLaunches" or failed):
            signals[item.launch + DAY + 1].append(item)
    start, end = (bar_number(plan[k] + "T00:00:00Z") for k in ("common_start", "common_end"))
    for clock in range(start, end + 1):
        account.step(clock, market, signals.get(clock, ()))
    if account.hold:
        raise ValueError("Residual exposure without a reliable terminal value")
    return account, sum(map(len, signals.values()))


def summarize(plan, account, opportunities):
    nav = pd.DataFrame(account.nav, columns=["clock", "cash", "equity", "open_positions"])
    curve = nav.set_index("clock").equity
    split = bar_number(plan["split"] + "T00:00:00Z")
    days = (curve.index[-1] - curve.index[0]) / DAY
    trades = account.finished
    weeks = {pd.Timestamp(t["entry"]).isocalendar()[:2] for t in trades}
    report = {
        "net_return": float(curve.iloc[-1] / curve.iloc[0] - 1),
        "annualized_net_return": float((curve.iloc[-1] / curve.iloc[0]) ** (365 / days) - 1),
        "max_drawdown": float((1 - curve / curve.cummax()).max()),
        "first_slice_return": float(curve.at[split] / curve.iloc[0] - 1),
        "second_slice_return": float(curve.iloc[-1] / curve.at[split] - 1),
        "opportunities": opportunities,
        "completed": len(trades),
        "entry_weeks": len(weeks),
        "catastrophic_losses": sum(e["kind"] == "collateral_loss" for e in account.events),
        "halted": account.halted,
    }
    return nav, report


def run():
    plan = read(PLAN)
    if (
        sha(Path(__file__)) != plan["replay_sha256"]
        or sha(ADMISSION) != plan["admission_registration_sha256"]
    ):
        raise ValueError("Frozen model changed")
    intake = read(OUT / "admission.json")
    market = {item.name: item for item in market_data(plan, intake)}
    results = {}
    for case in plan["cases"]:
        for cost in plan["cost_bps"]:
            key = f"{case}_{cost}bps"
            path = OUT / key
            path.mkdir()
            account, opportunities = simulate(plan, market, case, cost)
            nav, report = summarize(plan, account, opportunities)
            nav.to_parquet(path / "nav.parquet", index=False)
            save(path / "events.json", account.events)
            save(path / "closed.json", account.finished)
            results[key] = report
            print(key, report, flush=True)
    failed = {}
    for cost in plan["cost_bps"]:
        candidate, control = [results[f"{c}_{cost}bps"] for c in plan["cases"]]
        checks = {
            "full_net_excess": candidate["net_return"] > max(0, control["net_return"]),
            "positive_slices": min(
                candidate["first_slice_return"], candidate["second_slice_return"]
            )
            > 0,
            "sample": candidate["completed"] >= 30 and candidate["entry_weeks"] >= 12,
            "risk": (
                not candidate["halted"]
                and candidate["catastrophic_losses"] == 0
                and candidate["max_drawdown"] <= plan["maximum_observed_drawdown"]
            ),
        }
        failed[str(cost)] = [name for name, passes in checks.items() if not passes]
    save(
        HERE / "result.json",
        {
            "registration_sha256": sha(PLAN),
            "admission_sha256": sha(OUT / "admission.json"),
            "data_scope": {"admitted": intake["admitted"], "unknown": intake["unknown"]},
            "accounts": results,
            "failed_gates": failed,
            "historical_screen_passed": not any(failed.values()),
            "upside_screen_passed": results["FailedLaunch_40bps"]["annualized_net_return"] >= 0.25,
            "paper_eligible": False,
        },
    )


if __name__ == "__main__":
    {"register": register, "run": run}[sys.argv[1]]()
