"""Freeze and run the native X8 correlation allocation and comparison gates."""

import itertools
import math
import shutil
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi_profit_ramp_20261107 import study as a13
from research.crypto.nfi_solo_allocation_20261107 import long_mark
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
OUT = DATA / "nfi_correlation_allocation_20261107_v001"
A04 = a13.A04
PROTOCOL = HERE / "protocol.json"
RESULT = HERE / "result.json"
STRATEGY = "CorrelationTopBoost"
COSTS = (15, 8)
PARENTS = {
    "2024": a13.PLAN,
    "temporal": A04 / "temporal_registration.json",
    "long": A04 / "registration.json",
}


def register():
    if PROTOCOL.exists() or OUT.exists():
        raise FileExistsError("A14 already registered")
    (_, flat, _) = read(a13.PLAN)["candidates"]
    config = HERE / "config.json"
    save(config, {**read(checked(flat["config"])), "strategy": STRATEGY})
    candidate = {
        **flat,
        "name": STRATEGY,
        "strategy": STRATEGY,
        "config": record(config),
        "source_files": {**flat["source_files"], f"{STRATEGY}.py": record(HERE / f"{STRATEGY}.py")},
        "source_and_risk_behavior": "Raise Top Coins first stake as long overlap declines.",
    }
    plans = {}
    for stage, path in PARENTS.items():
        native = read(path)
        native.pop("baselines", None)
        plan_path = HERE / f"native_{stage}.json"
        save(
            plan_path,
            {
                **native,
                "schema": "freqtrade.research.nfi-correlation-allocation.v1",
                "registered_at": datetime.now(UTC).isoformat(),
                "candidates": [candidate],
                "total_cost_bps_per_side": list(COSTS),
                "orders_enabled": False,
            },
        )
        plans[stage] = record(plan_path)
    sources = (
        Path(__file__),
        HERE / f"{STRATEGY}.py",
        HERE / "evaluate.py",
        config,
        *PARENTS.values(),
        a13.RESULT,
        a13.OUT / "5m/admission.json",
        A04 / "temporal_result.json",
        A04 / "mark_result.json",
        A04 / "result.json",
        long_mark.study.HERE / "long_mark_result.json",
        long_mark.OUT / "admission.json",
        Path(temporal.__file__),
        Path(mark_valuation.__file__),
        mark_valuation.ORACLE,
        A04 / "audit.py",
    )
    save(
        PROTOCOL,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "plans": plans,
            "sources": [record(path) for path in sources],
            "hypothesis": (
                "Condition the aggressive first stake on the measured amount of positively "
                "correlated long inventory to retain extra return while reducing stacked risk."
            ),
            "rule": (
                "Pure native Top Coins long first stakes only. Use Pearson correlation of 30 "
                "daily log returns ending with the preceding completed UTC daily candle. "
                "At the callback, mark each existing filled long quantity at its last closed "
                "daily price and add pending entry notional at its placement price. Sum each "
                "long notional times max(0, correlation with incoming asset). Native short "
                "inventory does not offset the score. Divide by 30% of starting capital plus "
                "strictly prior closed campaigns' net PnL (30% multiplies the sum). Boost = "
                "1 + 0.5 * max(0, 1 - overlap / budget). Missing or zero-variance data or "
                "nonpositive realized capital means 1x. No leverage, signal, exit or add change."
            ),
            "gate": (
                "Run 2024 first at both costs. Require positive full return above original "
                "and return/5m drawdown above fixed 1.25x, full 5m drawdown <= original +1pp, "
                "higher return than original in every registered slice, >=60 natural closes, "
                ">=20 scoped fills and >=10 boosted scoped fills. If both costs pass, run "
                "65-day temporal comparison with the same economic gates but minimum 20 natural "
                "closes, 10 scoped fills and 5 boosted. If it passes, run 20-month historical "
                "comparison using the 2024 sample thresholds. Retain all failed runs."
            ),
            "known_history": (
                "The 2024 correlated drawdown incident and all original/flat returns in "
                "the three windows were known before designing A14. Historical adaptation, "
                "no independent forward evidence. Pure 1.5x no-inventory A08 is separate."
            ),
            "orders_enabled": False,
        },
    )


def check():
    protocol = read(PROTOCOL)
    for source in protocol["sources"]:
        checked(source)
    return {stage: checked(ref) for stage, ref in protocol["plans"].items()}


def correlation_audit(plan, folder, trades):  # noqa: C901
    tapes = read(checked(plan["data_receipts"]))
    daily = {}
    states, proposals = (
        jsonl(folder / filename)
        for filename in ("overlap-decisions.jsonl", "stake-decisions.jsonl")
    )
    longs = {(t["pair"], t["open_timestamp"]): t for t in trades if not t["is_short"]}
    trace = {}
    attempts = Counter()
    tied_holding_snapshots = 0

    def prices(pair, days):
        if pair not in daily:
            name = f"futures/{pair.replace('/', '_').replace(':', '_')}-1d-futures.feather"
            path = checked({"path": str(Path(plan["datadir"]) / name), "sha256": tapes[name]})
            daily[pair] = pd.read_feather(path).set_index("date").close.to_dict()
        return [daily[pair].get(d, math.nan) for d in days]

    def returns(p):
        if any(not math.isfinite(x) or x <= 0 for x in p):
            return []
        return [math.log(b / a) for a, b in itertools.pairwise(p)]

    def corr(a, b):
        if not a or not b:
            return None
        ax, bx = math.fsum(a) / len(a), math.fsum(b) / len(b)
        x, y = [v - ax for v in a], [v - bx for v in b]
        norm = math.hypot(*x) * math.hypot(*y)
        return (math.fsum(i * j for i, j in zip(x, y, strict=True)) / norm) if norm else None

    for state, order in zip(states, proposals, strict=True):
        if (state["time"], state["pair"]) != (order["time"], order["pair"]):
            raise ValueError("Overlap and stake proposal clocks differ")
        at = pd.Timestamp(state["time"])
        clock = at.value // 1_000_000
        cutoff = at.tz_convert("UTC").floor("D") - pd.Timedelta(days=1)
        if cutoff.isoformat() != state["last_closed_day"]:
            raise ValueError("Daily lookback cutoff differs")
        days = pd.date_range(end=cutoff, periods=31, freq="D")
        own = returns(prices(state["pair"], days))
        capital = plan["initial_equity_usdt"] + math.fsum(
            float(t["profit_abs"]) for t in trades if t["close_timestamp"] < clock
        )
        if not math.isclose(capital, state["realized_capital_usdt"], abs_tol=1e-8):
            raise ValueError("Strictly prior realized capital differs")
        observed = {}
        risk = []
        for h in state["longs"]:
            key = (h["pair"], pd.Timestamp(h["opened"]).value // 1_000_000)
            if key in observed or h["quantity"] < 0 or h["pending_usdt"] < 0:
                raise ValueError("Invalid long inventory snapshot")
            observed[key] = h["quantity"]
            if h["pending_usdt"]:
                attempts["snapshots_with_pending_entry"] += 1
            price = prices(h["pair"], days)
            rho = corr(own, returns(price))
            if (rho is None) != (h["rho"] is None) or (
                rho is not None and not math.isclose(rho, h["rho"], abs_tol=1e-10)
            ):
                raise ValueError("Independent rolling correlation differs")
            if h["prior_close"] is not None and h["prior_close"] != price[-1]:
                raise ValueError("Position marked with wrong prior price")
            risk.append(
                None
                if rho is None
                else max(0, rho) * (price[-1] * h["quantity"] + h["pending_usdt"])
            )
        for key in set(observed) | set(longs):
            native = longs.get(key)
            certain = possible = 0.0
            if native and native["open_timestamp"] <= clock <= native["close_timestamp"]:
                strictly_prior = [
                    o for o in native["orders"] if o["order_filled_timestamp"] < clock
                ]
                tied = [o for o in native["orders"] if o["order_filled_timestamp"] == clock]
                quantity = math.fsum(
                    float(o["amount"]) * (1 if o["ft_is_entry"] else -1) for o in strictly_prior
                )
                certain = quantity - math.fsum(
                    float(o["amount"]) for o in tied if not o["ft_is_entry"]
                )
                possible = quantity + math.fsum(
                    float(o["amount"]) for o in tied if o["ft_is_entry"]
                )
            q = observed.get(key, 0.0)
            if q < certain - 1e-8 or q > possible + 1e-8:
                raise ValueError(f"Inventory differs from causal native ledger at {at}: {key}/{q}")
            tied_holding_snapshots += int(possible - certain > 1e-8)
        overlap = (
            math.fsum(risk)
            if all(v is not None for v in risk) and own and np.std(own) > 0 and capital > 0
            else None
        )
        if (overlap is None) != (state["overlap_usdt"] is None) or (
            overlap is not None and not math.isclose(overlap, state["overlap_usdt"], abs_tol=1e-8)
        ):
            raise ValueError("Independent overlap notional differs")
        ratio = 1 if overlap is None else 1 + 0.5 * max(0, 1 - overlap / (0.30 * capital))
        stake = min(order["max_stake"], max(order["min_stake"] or 0, order["native_stake"] * ratio))
        if not math.isclose(ratio, order["multiplier"], abs_tol=1e-10) or not math.isclose(
            stake, order["requested_stake"], abs_tol=1e-8
        ):
            raise ValueError("Independent stake or bounds differ")
        key = (state["pair"], clock, order["tag"].strip())
        trace[key] = ratio
        attempts["sizing_callbacks"] += 1
    fills = [
        trace[(t["pair"], t["open_timestamp"], t["enter_tag"].strip())] for t in trades if scope(t)
    ]
    return {
        "attempts": dict(attempts),
        "same_clock_inventory_envelopes": tied_holding_snapshots,
        "scoped_fills": len(fills),
        "boosted_fills": sum(v > 1.000000001 for v in fills),
        "max_boost_fills": sum(v == 1.5 for v in fills),
        "mean_multiplier": math.fsum(fills) / len(fills),
        "overlap_sha256": sha(folder / "overlap-decisions.jsonl"),
        "stake_trace_sha256": sha(folder / "stake-decisions.jsonl"),
    }


def run_account(stage, plan_path, cost):
    plan = read(plan_path)
    (candidate,) = plan["candidates"]
    key = f"{stage}_{cost}bps"
    folder = OUT / key
    with folder.with_suffix(".log").open("x") as log:
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
                str(folder),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    summary = temporal.audit_one(plan, candidate, cost, folder)
    trades, _ = native_trades(plan, folder, STRATEGY, cost)
    summary.update(correlation_audit(plan, folder, trades))
    save(folder / "audit.json", summary)
    cache = folder / "lossless_cache"
    if cache.is_dir() and not cache.is_symlink():
        shutil.rmtree(cache)
    print(key, summary["periods"]["continuous"]["net_return"], summary["boosted_fills"], flush=True)
    return summary


if __name__ == "__main__":
    if sys.argv[1:] != ["register"]:
        raise SystemExit("Use register")
    register()
