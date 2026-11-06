"""Test a past-only listing-cohort timing gate with the frozen A05 cash account."""

import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.listing_funding_20261107 import replicate as a06
from research.crypto.listing_funding_20261107 import verify_regimes
from research.crypto.listing_reversal_20261107 import audit, catalog, coverage, replay
from research.crypto.nfi_strength_allocation_20261107.register import DATA, record
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked


HERE = Path(__file__).resolve().parent
OUT = DATA / "listing_cycle_20261107_v001"
POLICY = HERE / "registration.json"
RESULT = HERE / "result.json"
BARS_DAY = replay.DAY
LOOKBACK = 90 * BARS_DAY
MINIMUM_DAYS = 12
SOURCE_COST = 40
BUFFER = 2
WINDOWS = ("2024", "2025_2026", "reset_2026")
CASES = ("AllLaunches", "HalfRisk", "MatureCycle")


def register():
    if POLICY.exists() or OUT.exists():
        raise FileExistsError("Cycle experiment already frozen")
    sources = (
        Path(__file__),
        Path(replay.__file__),
        Path(audit.__file__),
        Path(a06.__file__),
        Path(verify_regimes.__file__),
        a06.PLAN,
        a06.HERE / "result.json",
        a06.HERE / "regime_result.json",
        a06.OUT / "admission.json",
        replay.PLAN,
        coverage.HERE / "coverage_result.json",
        coverage.HERE / "reset_2026_registration.json",
        coverage.HERE / "reset_2026_result.json",
        catalog.OUT / "admission.json",
    )
    save(
        POLICY,
        {
            "registered_utc": catalog.now(),
            "sources": [record(p) for p in sources],
            "windows": WINDOWS,
            "cases": CASES,
            "factor": (
                "Use the last 12 distinct UTC decision-date cohorts with released 72h short "
                "labels in 90 days. Each date gets one vote via its median across eligible "
                "symbols. Eligible = original 5m-quote 5m USDT threshold by 24h. "
                "Label uses the next 5m close at entry and first 5m close strictly after "
                "the 72h expiry, adverse slips and fees at 40bps each side, and actual "
                "published funds during the hold. Labels are fixed size and ignore stops, "
                "capacity and account cash. Release the whole UTC decision date on D+4 "
                "00:10 UTC. Never use a partially observed or still maturing cohort. "
                "When an unresolved admitted-universe listing is in a date cohort, omit "
                "its known decision date from feature history. If launch hour is unknown, "
                "omit the full calendar date following its first archive day. "
                "A median >0 enables short entries under the existing A05 account rules. "
                "Incomplete history holds cash. No pre-2024 or 2024-to-2025 history "
                "bridging. The 2026 account reset retains causal 2025 signal history."
            ),
            "controls": (
                "AllLaunches reproduces frozen original. HalfRisk uses the same "
                "entries but halves per-position and total collateral limits. MatureCycle "
                "uses original risk and filters entries only. Use the original model "
                "unmodified at both 15 and 40 bps. Report shared-market cohort selection, "
                "audited float NAV, exact funding, partial fills and halts."
            ),
            "gate": (
                "Both costs in both 2024 and continuous 2025-26 need positive full and "
                "both original time-slice returns, >=30 closed trades across >=12 weeks, "
                "no catastrophic loss or halt, 5m observed max drawdown <=35% and below full "
                "risk, net return > half risk, >=80% of positive full-risk net return "
                "retained, and return/drawdown >= both controls. "
                "Apply the same checks to overlapping 2026 empty-wallet reset with >=18 "
                "closes and >=8 weeks. Require >=25% annualized net at stress cost "
                "in each window. Original account economics and dates were known. "
                "Candidate and half-risk outputs were unobserved; all windows retrospective."
            ),
            "orders_enabled": False,
        },
    )


def observation(item, parent):
    entry = item.launch + BARS_DAY + 1
    end = entry + parent["hold_hours"] * replay.HOUR + 1
    fee = parent["fees_bps"] / 10000
    slip = (SOURCE_COST - parent["fees_bps"]) / 10000
    first = item.close[item.index(entry)] * (1 - slip)
    last = item.close[item.index(end)] * (1 + slip)
    carry = math.fsum(
        rate * mark
        for clock, events in item.funding.items()
        if entry <= clock <= end
        for actual, rate, mark in events
        if replay.iso(entry) < actual <= replay.iso(end)
    )
    return float((first - last - (first + last) * fee + carry) / first)


def signal_receipts(parent, markets, unknown_days):
    eligible = [m for m in markets.values() if m.day_quote >= parent["day_quote_minimum"]]
    groups = defaultdict(list)
    for market in eligible:
        day = (market.launch + BARS_DAY) // BARS_DAY * BARS_DAY
        groups[day].append((market.name, observation(market, parent)))
    published = {
        day + 4 * BARS_DAY + BUFFER: {
            "day": day,
            "names": sorted(n for n, _ in rows),
            "median": median(r for _, r in rows),
        }
        for day, rows in groups.items()
        if day not in unknown_days
    }
    signals = {}
    for item in eligible:
        decision = item.launch + BARS_DAY
        clocks = [t for t in sorted(published) if decision - LOOKBACK <= t <= decision][
            -MINIMUM_DAYS:
        ]
        values = [published[t]["median"] for t in clocks]
        score = median(values) if len(values) == MINIMUM_DAYS else None
        signals[item.name] = {
            "decision": decision,
            "history_releases": clocks,
            "score": score,
            "accepted": score is not None and score > 0,
        }
    return published, signals


def independent_signals(parent, panel, signals, published):
    # Rebuild each released cohort from the sealed pandas clocks instead of
    # borrowing bar-number offsets or the model's funding dictionary.
    fee = parent["fees_bps"] / 10000
    slip = (SOURCE_COST - parent["fees_bps"]) / 10000
    for release, cohort in published.items():
        day = pd.Timestamp(replay.iso(cohort["day"]))
        expected_release = day + pd.Timedelta(days=4, minutes=10)
        if replay.iso(release) != expected_release.isoformat():
            raise ValueError("Cohort release differs")
        labels = []
        for name in cohort["names"]:
            trade, _, funding, launch = panel[name]
            start = pd.Timestamp(replay.iso(launch))
            decision = start + pd.Timedelta(days=1)
            opened = decision + pd.Timedelta(minutes=5)
            closed = opened + pd.Timedelta(hours=72, minutes=5)
            prices = trade.close
            # OHLC indexes are candle starts; decisions and fills are at closes.
            first, last = (
                prices.at[t - pd.Timedelta(minutes=5)] * (1 + sign * slip)
                for t, sign in ((opened, -1), (closed, 1))
            )
            rates = funding[(funding.index > opened) & (funding.index <= closed)]
            cash = (rates.rate * rates.mark).sum()
            labels.append((first - last - fee * (first + last) + cash) / first)
            if decision.floor("D") != day or not closed < expected_release:
                raise ValueError("Source observation was not fully released")
        if not math.isclose(median(labels), cohort["median"], abs_tol=1e-11):
            raise ValueError("Cohort label differs")
    for name, row in signals.items():
        asof = panel[name][3] + BARS_DAY
        candidates = sorted(t for t in published if 0 <= asof - t <= LOOKBACK)[-MINIMUM_DAYS:]
        values = [published[t]["median"] for t in candidates]
        score = median(values) if len(values) == MINIMUM_DAYS else None
        if (
            row["decision"] != asof
            or row["history_releases"] != candidates
            or row["score"] != score
            or row["accepted"] != (score is not None and score > 0)
        ):
            raise ValueError("Candidate history uses a different cutoff or population")


def window_inputs():
    parent, older_panel, older_market, _ = a06.all_inputs()
    intake = read(a06.PLAN)
    old_names = {r["symbol"]: r for r in intake["symbols"]["2024"]}
    old = {s: m for s, m in older_market.items() if s in old_names}
    missing = sorted(set(old_names) - set(old))
    unknown_days = {
        replay.bar_number(pd.Timestamp(old_names[s]["first_archive_day"], tz="UTC")) + BARS_DAY
        for s in missing
    }
    earlier = {
        **parent,
        **{k: intake["windows"]["2024"][k] for k in ("common_start", "split", "common_end")},
    }
    _, later_panel, later = verify_regimes.original_inputs()
    reset_source = read(coverage.HERE / "reset_2026_registration.json")
    reset = {**parent, **{k: reset_source[k] for k in ("common_start", "split", "common_end")}}
    return {
        "2024": (earlier, older_panel, old, unknown_days, missing),
        "2025_2026": (parent, later_panel, later, set(), []),
        "reset_2026": (reset, later_panel, later, set(), []),
    }


def run_account(folder, contract, panel, pool, case, cost):
    key = f"{case}_{cost}bps"
    path = folder / key
    path.mkdir()
    account, opportunities = replay.simulate(contract, pool, "AllLaunches", cost)
    nav, summary = replay.summarize(contract, account, opportunities)
    nav.to_parquet(path / "nav.parquet", index=False)
    save(path / "events.json", account.events)
    save(path / "closed.json", account.finished)
    verification = audit.audit_account(contract, panel, key, summary)
    print(folder.name, key, summary["net_return"], summary["max_drawdown"], flush=True)
    return {"economics": summary, "verification": verification}


def compare(window, accounts, costs):
    comparison = {}
    min_trades, min_weeks = (18, 8) if window == "reset_2026" else (30, 12)
    for cost in costs:
        full, half, candidate = (accounts[f"{k}_{cost}bps"]["economics"] for k in CASES)
        net, dd = candidate["net_return"], candidate["max_drawdown"]
        comparison[str(cost)] = {
            "positive": min(net, candidate["first_slice_return"], candidate["second_slice_return"])
            > 0,
            "sample": candidate["completed"] >= min_trades
            and candidate["entry_weeks"] >= min_weeks,
            "risk": dd <= 0.35
            and dd < full["max_drawdown"]
            and not candidate["halted"]
            and not candidate["catastrophic_losses"],
            "net_excess": net > half["net_return"] and net >= 0.8 * max(0, full["net_return"]),
            "risk_adjusted": all(
                net * r["max_drawdown"] >= r["net_return"] * dd for r in (full, half)
            ),
            "stress_annualized": cost != SOURCE_COST or candidate["annualized_net_return"] >= 0.25,
        }
    return comparison


def run():
    policy = read(POLICY)
    for ref in policy["sources"]:
        checked(ref)
    OUT.mkdir()
    report = {}
    signals_later = None
    for window, (contract, panel, market, bad_dates, missing) in window_inputs().items():
        folder = OUT / window
        folder.mkdir()
        audit.OUT = folder
        if window == "reset_2026":
            released, signals = signals_later
        else:
            released, signals = signal_receipts(contract, market, bad_dates)
            independent_signals(contract, panel, signals, released)
            if window == "2025_2026":
                signals_later = released, signals
        receipt = folder / "signals.json"
        save(receipt, {"released": released, "signals": signals})
        start = replay.bar_number(pd.Timestamp(contract["common_start"], tz="UTC"))
        pool = {s: m for s, m in market.items() if m.launch >= start}
        selected = {s: m for s, m in pool.items() if signals.get(s, {}).get("accepted", False)}
        half = {
            **contract,
            "stake_fraction": contract["stake_fraction"] / 2,
            "margin_fraction": contract["margin_fraction"] / 2,
        }
        accounts = {
            f"{case}_{cost}bps": run_account(folder, rules, panel, chosen, case, cost)
            for case, rules, chosen in zip(
                CASES, (contract, half, contract), (pool, pool, selected), strict=True
            )
            for cost in contract["cost_bps"]
        }
        report[window] = {
            "admitted": len(pool),
            "eligible": len(set(signals) & set(pool)),
            "selected": len(selected),
            "missing": missing,
            "omitted_decision_days": sorted(bad_dates),
            "signal_receipt": record(receipt),
            "accounts": accounts,
            "checks": compare(window, accounts, contract["cost_bps"]),
        }
    passed = all(
        all(row.values()) for window in report.values() for row in window["checks"].values()
    )
    save(
        RESULT,
        {
            "registration_sha256": sha(POLICY),
            "windows": report,
            "advance": passed,
            "paper_eligible": False,
        },
    )


if __name__ == "__main__":
    {"register": register, "run": run}[sys.argv[1]]()
