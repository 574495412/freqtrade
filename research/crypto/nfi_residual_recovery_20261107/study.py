"""Freeze an X8 entry attribution and independent native recovery accounts."""

import math
import shutil
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi_profit_ramp_20261107 import study as a13
from research.crypto.nfi_solo_allocation_20261107 import long_mark
from research.crypto.nfi_solo_allocation_20261107.run import jsonl, scope
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation, temporal
from research.crypto.nfi_strength_allocation_20261107.audit import native_trades, read
from research.crypto.nfi_strength_allocation_20261107.register import (
    BASELINES,
    DATA,
    PARENT,
    record,
)
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked,
    save,
    sha,
)


HERE = Path(__file__).resolve().parent
A04 = a13.A04
OUT = DATA / "nfi_residual_recovery_20261107_v001"
PROTOCOL = HERE / "protocol.json"
RESULT = HERE / "result.json"
STRATEGY = "ResidualRecovery"
ORIGINAL = a13.ORIGINAL
COSTS = (15, 8)
PARENTS = {
    "2024": a13.PLAN,
    "temporal": A04 / "temporal_registration.json",
    "long": A04 / "registration.json",
}


def folder(stage, cost):
    if stage == "2024":
        return a13.OUT / f"{ORIGINAL}_{cost}bps"
    if stage == "temporal":
        return temporal.OUT / f"{ORIGINAL}_{cost}bps"
    return BASELINES[cost]


def register():
    if PROTOCOL.exists() or OUT.exists():
        raise FileExistsError("A15 already registered")
    template = read(PARENTS["temporal"])
    original, flat = template["candidates"]
    config = HERE / "ResidualRecovery.json"
    baseline_cfg = read(checked(original["config"]))
    stake_cfg = read(checked(flat["config"]))
    if {**stake_cfg, "strategy": baseline_cfg["strategy"]} != baseline_cfg:
        raise ValueError("Fixed reference does not use original config")
    save(config, {**stake_cfg, "strategy": STRATEGY})
    candidate = {
        **flat,
        "name": STRATEGY,
        "strategy": STRATEGY,
        "config": record(config),
        "source_files": {**flat["source_files"], f"{STRATEGY}.py": record(HERE / f"{STRATEGY}.py")},
        "source_and_risk_behavior": "Double the Top Coins first stake on residual recovery.",
    }
    runs = {}
    for stage in ("temporal", "long"):
        old = read(PARENTS[stage])
        old.pop("baselines", None)
        plan = HERE / f"native_{stage}.json"
        save(
            plan,
            {
                **old,
                "schema": "freqtrade.research.nfi-residual-recovery.v1",
                "registered_at": datetime.now(UTC).isoformat(),
                "candidates": [candidate],
                "total_cost_bps_per_side": list(COSTS),
                "orders_enabled": False,
            },
        )
        runs[stage] = record(plan)
    archives = {}
    for stage in PARENTS:
        for cost in COSTS:
            path = folder(stage, cost)
            (archive,) = (path / "native_results").glob("*.zip")
            archives[f"{stage}_{cost}bps"] = record(archive)
    sources = (
        Path(__file__),
        HERE / f"{STRATEGY}.py",
        HERE / "evaluate.py",
        HERE.parent / "nfi_correlation_allocation_20261107/evaluate.py",
        Path(long_mark.__file__),
        config,
        *PARENTS.values(),
        a13.RESULT,
        A04 / "temporal_result.json",
        A04 / "mark_result.json",
        A04 / "result.json",
        long_mark.study.HERE / "long_mark_result.json",
        long_mark.OUT / "admission.json",
        Path(mark_valuation.__file__),
        Path(temporal.__file__),
        mark_valuation.ORACLE,
        A04 / "audit.py",
    )
    save(
        PROTOCOL,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "runs": runs,
            "sources": [record(path) for path in sources],
            "original_archives": archives,
            "hypothesis": (
                "On native Top Coins long entries, a market-adjusted 7d pullback followed "
                "by positive last-day residual can identify higher-return recoveries."
            ),
            "rule": (
                "At each native entry decision, use 68 exact consecutive completed UTC closes "
                "through the prior day. Fit asset-on-BTC OLS slope with intercept on the oldest "
                "60 log returns, strictly before the final seven. For the final seven compute "
                "asset log return minus slope times BTC log return, excluding the fitted intercept "
                "from attribution. If their sum is <= log(0.97) and the last residual > 0, apply "
                "2.0x first stake to pure Top Coins longs only; else 1.0x. Missing, nonpositive "
                "closes and zero BTC variance use 1x. Original wallet bounds, leverage, "
                "position adjustments and exits remain."
            ),
            "diagnostic": (
                "Attribute sealed original pure Top Coins natural closes in 2024, 2025-Aug2026, "
                "and the 65-day comparison. Compare total realized net PnL divided by summed "
                "all filled entry notionals in flagged vs unflagged groups. Preserve unknowns, "
                "terminal inventory and force exits excluded. This compares original realized "
                "paths rather than a counterfactual account. For long-stage admission require "
                "at least 10 flagged campaigns and positive per-entry-capital edge versus "
                "unflagged in 2024 and 2025-Aug2026 at both costs."
            ),
            "economic_gate": (
                "Run 65-day native accounts regardless of attribution result. Require at both "
                "costs full positive net return above original and 5m drawdown <= original "
                "+1pp, full return/5m drawdown above fixed 1.25x, >=20 natural closes, "
                ">=10 scoped first fills and >=4 boosted fills. Only run the 20-month candidate "
                "if both this gate and diagnostic gate pass. Long requires same full gates, "
                "higher than original in both slices and minimum 60 closes, 20 scoped, 10 boosted."
            ),
            "known_history": (
                "Control total returns in these windows were previously known. No thresholds "
                "selected using recovery-labeled economics. Both stages are retrospective."
            ),
            "orders_enabled": False,
        },
    )


def checked_protocol():
    policy = read(PROTOCOL)
    for source in policy["sources"]:
        checked(source)
    return policy


class IndependentFactor:
    def __init__(self, plan):
        self.data = Path(plan["datadir"])
        self.receipts = read(checked(plan["data_receipts"]))
        self.prices = {}

    def closes(self, pair, days):
        if pair not in self.prices:
            stem = pair.replace("/", "_").replace(":", "_")
            rel = f"futures/{stem}-1d-futures.feather"
            path = checked({"path": str(self.data / rel), "sha256": self.receipts[rel]})
            self.prices[pair] = pd.read_feather(path).set_index("date").close.to_dict()
        return [self.prices[pair].get(d, math.nan) for d in days]

    def factor(self, pair, at):
        day = at.tz_convert("UTC").floor("D") - pd.Timedelta(days=1)
        dates = pd.date_range(end=day, periods=68, freq="D")
        asset, btc = (self.closes(s, dates) for s in (pair, "BTC/USDT:USDT"))
        if any(not math.isfinite(x) or x <= 0 for x in (*asset, *btc)):
            return day, (None, None, None)
        a, b = ([math.log(y / x) for x, y in pairwise(p)] for p in (asset, btc))
        mean_a, mean_b = math.fsum(a[:60]) / 60, math.fsum(b[:60]) / 60
        ss = math.fsum((x - mean_b) ** 2 for x in b[:60])
        if not ss:
            return day, (None, None, None)
        beta = (
            math.fsum((x - mean_a) * (y - mean_b) for x, y in zip(a[:60], b[:60], strict=True)) / ss
        )
        errors = [x - beta * y for x, y in zip(a[-7:], b[-7:], strict=True)]
        return day, (beta, math.fsum(errors), errors[-1])


def signal(values):
    _, recent, latest = values
    return recent is not None and recent <= math.log(0.97) and latest > 0


def attribute(policy):
    results = {}
    for stage, path in PARENTS.items():
        plan = read(path)
        factor = IndependentFactor(plan)
        stop = pd.Timestamp(plan["comparison_end"]).value // 1_000_000
        for cost in COSTS:
            key = f"{stage}_{cost}bps"
            reference = checked(policy["original_archives"][key])
            trades, original_audit = native_trades(
                plan, reference.parents[1], "NostalgiaForInfinityX8", cost
            )
            values = {"flagged": [], "unflagged": [], "unknown": []}
            excluded = 0
            for t in trades:
                if not scope(t):
                    continue
                if t["exit_reason"] == "force_exit" or t["close_timestamp"] >= stop:
                    excluded += 1
                    continue
                at = pd.Timestamp(t["open_timestamp"], unit="ms", tz="UTC")
                _, obs = factor.factor(t["pair"], at)
                tag = "unknown" if obs[0] is None else "flagged" if signal(obs) else "unflagged"
                size = math.fsum(
                    float(o["amount"] * o["safe_price"]) for o in t["orders"] if o["ft_is_entry"]
                )
                values[tag].append((float(t["profit_abs"]), size, str(at.date())))
            groups = {}
            for name, rows in values.items():
                pnl = math.fsum(r[0] for r in rows)
                stake = math.fsum(r[1] for r in rows)
                groups[name] = {
                    "natural_closes": len(rows),
                    "entry_days": len({r[2] for r in rows}),
                    "net_pnl_usdt": pnl,
                    "filled_entry_notional_usdt": stake,
                    "pnl_per_entry_notional": pnl / stake if stake else None,
                }
            flag, other = groups["flagged"], groups["unflagged"]
            accepted = flag["natural_closes"] >= 10 and (
                flag["pnl_per_entry_notional"] > max(0.0, other["pnl_per_entry_notional"] or 0)
            )
            results[key] = {
                "groups": groups,
                "excluded_terminal": excluded,
                "passed": accepted,
                "archive_sha256": original_audit["archive_sha256"],
            }
    return results


def verify_factor(plan, path, trades):
    factor = IndependentFactor(plan)
    states, stakes = (
        jsonl(path / name) for name in ("residual-recovery.jsonl", "stake-decisions.jsonl")
    )
    signals = {}
    attempts = Counter()
    for state, order in zip(states, stakes, strict=True):
        at = pd.Timestamp(state["time"])
        pair = state["pair"]
        if (pair, state["time"]) != (order["pair"], order["time"]):
            raise ValueError("Feature and sizing clocks differ")
        day, values = factor.factor(pair, at)
        if state["prior_closed_day"] != day.isoformat():
            raise ValueError("Incorrect daily feature cutoff")
        for key, expected in zip(("beta", "residual_7d", "residual_1d"), values, strict=True):
            observed = state[key]
            if (expected is None) != (observed is None) or (
                expected is not None and not np.isclose(expected, observed, rtol=1e-9, atol=1e-12)
            ):
                raise ValueError(f"Independent factor differs on {key}")
        multiplier = 2.0 if signal(values) else 1.0
        request = min(
            order["max_stake"], max(order["min_stake"] or 0, order["native_stake"] * multiplier)
        )
        if multiplier != order["multiplier"] or not math.isclose(
            request, order["requested_stake"], abs_tol=1e-9
        ):
            raise ValueError("Stake does not match registered sizing rule")
        clock = at.value // 1_000_000
        signals[(pair, clock, order["tag"].strip())] = multiplier
        attempts[str(multiplier)] += 1
    fills = Counter(
        str(signals[(t["pair"], t["open_timestamp"], t["enter_tag"].strip())])
        for t in trades
        if scope(t)
    )
    return {
        "attempted_multipliers": dict(attempts),
        "filled_multipliers": dict(fills),
        "scoped_fills": sum(fills.values()),
        "boosted_fills": fills["2.0"],
        "feature_trace_sha256": sha(path / "residual-recovery.jsonl"),
        "stake_trace_sha256": sha(path / "stake-decisions.jsonl"),
    }


def run_account(plan_path, stage, cost):
    plan = read(plan_path)
    (case,) = plan["candidates"]
    path = OUT / f"{stage}_{cost}bps"
    with path.with_suffix(".log").open("x") as log:
        subprocess.run(
            [
                sys.executable,
                "-B",
                str(PARENT / "run_wide_comparison.py"),
                "--plan",
                str(plan_path),
                "--candidate",
                STRATEGY,
                "--cost-bps",
                str(cost),
                "--output",
                str(path),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    summary = temporal.audit_one(plan, case, cost, path)
    trades, _ = native_trades(plan, path, STRATEGY, cost)
    summary.update(verify_factor(plan, path, trades))
    save(path / "audit.json", summary)
    cache = path / "lossless_cache"
    if cache.is_dir() and not cache.is_symlink():
        shutil.rmtree(cache)
    print(
        stage,
        cost,
        summary["periods"]["continuous"]["net_return"],
        summary["filled_multipliers"],
        flush=True,
    )
    return summary


if __name__ == "__main__":
    if sys.argv[1:] != ["register"]:
        raise SystemExit("Use register")
    register()
