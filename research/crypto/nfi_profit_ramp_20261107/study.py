"""Revalidate native X8 sizing on 2024 before admitting profit-conditioned risk."""

import math
import shutil
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from research.crypto.nfi_solo_allocation_20261107.run import jsonl, scope
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation, temporal
from research.crypto.nfi_strength_allocation_20261107.audit import native_trades, read
from research.crypto.nfi_strength_allocation_20261107.register import DATA, PARENT, record
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked,
    save,
    sha,
)


HERE = Path(__file__).resolve().parent
A04 = HERE.parent / "nfi_strength_allocation_20261107"
OUT = DATA / "nfi_profit_ramp_20261107_v001"
PLAN = HERE / "native_2024.json"
PROTOCOL = HERE / "protocol.json"
RESULT = HERE / "result.json"
ORIGINAL, FIXED, CANDIDATE = "nfi-x8-1x", "FlatTopBoost", "ProfitRamp"
COSTS = (15, 8)


def register():
    if PROTOCOL.exists() or OUT.exists():
        raise FileExistsError("A13 already registered")
    historical = read(A04 / "registration.json")
    short = read(A04 / "temporal_registration.json")
    original, flat = (
        next(c for c in short["candidates"] if c["name"] == s) for s in (ORIGINAL, FIXED)
    )
    original_cfg = read(checked(original["config"]))
    flat_cfg = read(checked(flat["config"]))
    if {**flat_cfg, "strategy": original_cfg["strategy"]} != original_cfg:
        raise ValueError("Base and flat config differ")
    if (
        historical["pairs"] != short["pairs"]
        or historical["engine_commit"] != short["engine_commit"]
    ):
        raise ValueError("Native contracts differ")
    config = HERE / "ProfitRamp.json"
    save(config, {**flat_cfg, "strategy": CANDIDATE})
    candidate = {
        **flat,
        "name": CANDIDATE,
        "strategy": CANDIDATE,
        "config": record(config),
        "source_files": {**flat["source_files"], "ProfitRamp.py": record(HERE / "ProfitRamp.py")},
        "source_and_risk_behavior": "First stake adds a bounded increment from realized profit.",
    }
    start, middle, finish = ("2024-01-01T00:00:00Z", "2024-07-01T00:00:00Z", "2024-12-31T23:00:00Z")
    runtime = {
        k: v for k, v in historical.items() if k not in ("baselines", "decision", "hypothesis")
    }
    save(
        PLAN,
        {
            **runtime,
            "schema": "freqtrade.research.nfi-profit-ramp-2024.v1",
            "registered_at": datetime.now(UTC).isoformat(),
            "start": start,
            "end": "2025-01-01T00:00:00Z",
            "comparison_end": finish,
            "periods": [
                {"name": "continuous", "start": start, "end": finish},
                {"name": "H1", "start": start, "end": middle},
                {"name": "H2", "start": middle, "end": finish},
            ],
            "candidates": [original, flat, candidate],
            "total_cost_bps_per_side": list(COSTS),
            "orders_enabled": False,
        },
    )
    sources = (
        Path(__file__),
        HERE / "marks.py",
        HERE / "ProfitRamp.py",
        config,
        PLAN,
        A04 / "registration.json",
        A04 / "temporal_registration.json",
        A04 / "mark_result.json",
        A04 / "temporal_result.json",
        A04 / "result.json",
        Path(temporal.__file__),
        Path(mark_valuation.__file__),
        mark_valuation.ORACLE,
        A04 / "audit.py",
    )
    save(
        PROTOCOL,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "sources": [record(path) for path in sources],
            "hypothesis": (
                "Confirm the strongest fixed sizing rule on a different calendar window "
                "before taking more risk, and test whether increases based on strictly "
                "prior realized campaign profit improve its account economics."
            ),
            "rule": (
                "Native first stakes of pure Top Coins longs only. Flat is unchanged 1.25x. "
                "ProfitRamp uses 1.25 + min(0.50, max(0, closed_net_pnl_usdt)/500), so +250 "
                "USDT in strictly earlier closed campaigns reaches 1.75x. Net PnL includes "
                "native fees/funding. No currently open campaign or same-clock close is "
                "admitted. The original wallet/price constraints, 1x leverage, entries, "
                "adjustments and exits remain. The profit state can decrease after losses."
            ),
            "baseline_gate": (
                "At both costs fixed 1.25x must have full positive return, higher net return "
                "than original in full and both slices, higher full net return/5m mark "
                "drawdown, at most +1pp 5m drawdown versus original, at least 60 natural "
                "closes and 20 scoped filled campaigns. Stop after the four controls if "
                "any gate fails. Do not change thresholds after observing economics."
            ),
            "profit_ramp_gate": (
                "Run both costs only after fixed sizing passes. Require full positive "
                "return, higher net return than fixed in full and both slices, higher full "
                "return/5m drawdown than fixed, within +1pp 5m drawdown versus original, "
                "at least 60 natural closes, 20 scoped fills and at least 10 ramped fills. "
                "Both comparison stages use frozen 2024 exact calendars."
            ),
            "known_history": (
                "The 2025-August 2026 and September-November 2026 fixed X8 results were "
                "known before registration. This exact 2024 X8 sizing matchup was not "
                "used to choose the original 1.25x overlay. The current X8 source and "
                "frozen market rules postdate the test window; retrospective, not live OOS."
            ),
            "orders_enabled": False,
        },
    )


def check():
    for source in read(PROTOCOL)["sources"]:
        checked(source)
    return read(PLAN)


def audit_ramp(folder, trades):
    states = jsonl(folder / "realized-profit.jsonl")
    proposals = jsonl(folder / "stake-decisions.jsonl")
    completed = sorted(trades, key=lambda t: t["close_timestamp"])
    observed = {}
    i, balance = 0, Decimal(0)
    for state, order in zip(states, proposals, strict=True):
        if (state["time"], state["pair"]) != (order["time"], order["pair"]):
            raise ValueError("Profit and sizing clocks differ")
        clock = pd.Timestamp(state["time"]).value // 1_000_000
        while i < len(completed) and completed[i]["close_timestamp"] < clock:
            balance += completed[i]["profit_abs"]
            i += 1
        if not math.isclose(float(balance), state["realized_usdt"], abs_tol=1e-8) or (
            i != state["strictly_prior_closes"]
        ):
            raise ValueError("Wallet history differs from prior native campaign closes")
        multiplier = 1.25 + min(0.5, max(0, float(balance)) / 500)
        stake = min(
            order["max_stake"], max(order["min_stake"] or 0, order["native_stake"] * multiplier)
        )
        if not math.isclose(multiplier, order["multiplier"], abs_tol=1e-12) or not math.isclose(
            stake, order["requested_stake"], abs_tol=1e-9
        ):
            raise ValueError("Realized-PnL stake differs")
        observed[(state["pair"], clock, order["tag"].strip())] = multiplier
    filled = Counter(
        "ramped"
        if observed[(t["pair"], t["open_timestamp"], t["enter_tag"].strip())] > 1.25
        else "base_1.25x"
        for t in trades
        if scope(t)
    )
    return {
        "matched_filled_top_coin_campaigns": sum(filled.values()),
        "ramped_fills": filled["ramped"],
        "fill_buckets": dict(filled),
        "realized_history_sha256": sha(folder / "realized-profit.jsonl"),
        "sizing_trace_sha256": sha(folder / "stake-decisions.jsonl"),
    }


def account(plan, name, cost):
    case = next(c for c in plan["candidates"] if c["name"] == name)
    key = f"{name}_{cost}bps"
    folder = OUT / key
    with folder.with_suffix(".log").open("x") as log:
        subprocess.run(
            [
                sys.executable,
                "-B",
                str(PARENT / "run_wide_comparison.py"),
                "--plan",
                str(PLAN),
                "--candidate",
                name,
                "--cost-bps",
                str(cost),
                "--output",
                str(folder),
            ],
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    summary = temporal.audit_one(plan, case, cost, folder)
    if name == CANDIDATE:
        trades, _ = native_trades(plan, folder, case["strategy"], cost)
        summary.update(audit_ramp(folder, trades))
    save(folder / "audit.json", summary)
    cache = folder / "lossless_cache"
    if cache.is_dir() and not cache.is_symlink():
        shutil.rmtree(cache)
    print(key, summary["periods"]["continuous"]["net_return"], flush=True)
    return key, summary


def gates(accounts, marks, name, reference):
    rows = {}
    for cost in COSTS:
        key, base, original = (f"{s}_{cost}bps" for s in (name, reference, ORIGINAL))
        candidate, compare = (accounts[k]["periods"] for k in (key, base))
        full = candidate["continuous"]
        dd, base_dd, original_dd = (marks[k]["drawdown_5m"] for k in (key, base, original))
        tests = {
            "returns": full["net_return"] > 0
            and all(p["net_return"] > compare[k]["net_return"] for k, p in candidate.items()),
            "return_to_drawdown": full["net_return"] * base_dd
            > compare["continuous"]["net_return"] * dd,
            "risk": dd - original_dd <= 0.01,
            "sample": full["completed_campaigns"] >= 60
            and accounts[key]["matched_filled_top_coin_campaigns"] >= 20,
        }
        if name == CANDIDATE:
            tests["ramped_fills"] = accounts[key]["ramped_fills"] >= 10
        rows[str(cost)] = tests
    return rows


def controls():
    OUT.mkdir(exist_ok=True)

    plan = check()
    accounts = dict(account(plan, name, cost) for cost in COSTS for name in (ORIGINAL, FIXED))
    save(OUT / "controls.json", accounts)


def finish():
    from research.crypto.nfi_profit_ramp_20261107 import marks as valuation

    plan = check()
    accounts = read(OUT / "controls.json")
    marks = valuation.revalue(plan, accounts)
    checks = gates(accounts, marks, FIXED, ORIGINAL)
    report = {
        "protocol_sha256": sha(PROTOCOL),
        "marks_admission_sha256": sha(OUT / "5m/admission.json"),
        "accounts": accounts,
        "mark_results": marks,
        "fixed_gates": checks,
        "ramp_admitted": all(all(r.values()) for r in checks.values()),
        "paper_eligible": False,
    }
    save(RESULT, report)
    print("2024 fixed gates", checks, flush=True)
    if not report["ramp_admitted"]:
        return
    aggressive = dict(account(plan, CANDIDATE, cost) for cost in COSTS)
    accounts.update(aggressive)
    marks.update(valuation.revalue(plan, aggressive))
    report["ramp_gates"] = gates(accounts, marks, CANDIDATE, FIXED)
    save(RESULT, report)
    print("2024 ramp gates", report["ramp_gates"], flush=True)


if __name__ == "__main__":
    {"register": register, "controls": controls, "finish": finish}[sys.argv[1]]()
