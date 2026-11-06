"""Freeze, run, and reconcile native NFI accounts for BTC volatility timing."""

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

from research.crypto.nfi_solo_allocation_20261107 import long_mark
from research.crypto.nfi_solo_allocation_20261107 import run as previous
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
OUT = DATA / "nfi_btc_vol_allocation_20261107_v001"
A04 = previous.A04
REGISTRATION = HERE / "protocol.json"
RESULT = HERE / "result.json"
STRATEGY = "BtcVolTopBoost"
BTC_DAILY = "futures/BTC_USDT_USDT-1d-futures.feather"
SHORT_PLAN = HERE / "temporal_native.json"
LONG_PLAN = HERE / "long_native.json"


def register():
    if REGISTRATION.exists() or OUT.exists():
        raise FileExistsError("A09 already registered")
    old_short, old_long = read(previous.SHORT_PLAN), read(previous.LONG_PLAN)
    (prior,) = old_short["candidates"]
    config = read(checked(prior["config"]))
    config["strategy"] = STRATEGY
    config_path = HERE / "config.json"
    save(config_path, config)
    candidate = {
        **prior,
        "name": STRATEGY,
        "strategy": STRATEGY,
        "config": record(config_path),
        "source_files": {
            name: source
            for name, source in prior["source_files"].items()
            if name != "SoloTopBoost.py"
        }
        | {f"{STRATEGY}.py": record(HERE / f"{STRATEGY}.py")},
        "source_and_risk_behavior": "Native X8 with first-stake boost based on BTC volatility.",
    }
    for parent, target in ((old_short, SHORT_PLAN), (old_long, LONG_PLAN)):
        save(
            target,
            {
                **parent,
                "schema": "freqtrade.research.nfi-btc-vol-allocation.v1",
                "registered_at": datetime.now(UTC).isoformat(),
                "candidates": [candidate],
            },
        )
    sources = (
        Path(__file__),
        HERE / "evaluate.py",
        HERE / f"{STRATEGY}.py",
        SHORT_PLAN,
        LONG_PLAN,
        A04 / "result.json",
        A04 / "temporal_result.json",
        A04 / "mark_result.json",
        previous.RESULT,
        previous.HERE / "long_mark_result.json",
        long_mark.OUT / "admission.json",
        Path(previous.__file__),
        Path(long_mark.__file__),
        Path(temporal.__file__),
        Path(mark_valuation.__file__),
        mark_valuation.ORACLE,
        A04 / "audit.py",
    )
    save(
        REGISTRATION,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "sources": [record(path) for path in sources],
            "motivation": "Moreira and Muir, Volatility Managed Portfolios, doi:10.3386/w22208",
            "hypothesis": (
                "BTC calm periods can support a more aggressive X8 first stake than the "
                "fixed 1.25x control while avoiding full-size increases in volatile periods."
            ),
            "rule": (
                "Only pure native Top Coins long first stakes. At a UTC decision day d, "
                "use sqrt(sum of squared log close returns)) for the seven BTC daily "
                "returns ending on the d-1 candle. Compare to the median of 90 trailing "
                "7-return observations ending on d-8 down to d-97. The reference and "
                "latest 7-return window have no shared returns. If both values are "
                "positive and finite and the latest is at most the median, use 1.75x "
                "the native first stake, subject to original wallet and exchange limits. "
                "Use 1x otherwise, including missing prices. Use no interpolated candles. "
                "No change to other modes, signals, direction, leverage, or position callbacks."
            ),
            "gate": (
                "At each of 8 and 15 bps in the 65-day window require full positive net "
                "return above original, 5m mark drawdown at most 1pp above original, "
                "return divided by 5m drawdown above both fixed 1.25x and A08, at least "
                "20 natural closes, 10 matched scoped campaigns, and nonzero boosted fills. "
                "Only after passing both costs, run the 20-month window at both costs. "
                "Require each net return above original for full and both calendar slices, "
                "full 5m drawdown at most 1pp above original, and full return/5m drawdown "
                "above both fixed 1.25x and A08. Preserve all outcomes."
            ),
            "known_history": (
                "Original, fixed, and A08 controls in both reused retrospective windows "
                "were known before registration. Neither window is blind to strategy family."
            ),
            "candidate_economics_observed": False,
            "orders_enabled": False,
        },
    )


def audit_volatility(plan, folder, trades):
    receipts = read(checked(plan["data_receipts"]))
    path = checked(
        {
            "path": str(Path(plan["datadir"]) / BTC_DAILY),
            "sha256": receipts[BTC_DAILY],
        }
    )
    prices = pd.read_feather(path).set_index("date").close.to_dict()

    def norm(end):
        days = (end - pd.Timedelta(days=i) for i in range(7, -1, -1))
        closes = [prices.get(day, math.nan) for day in days]
        if any(not math.isfinite(p) or p <= 0 for p in closes):
            return math.nan
        return math.hypot(*(math.log(b / a) for a, b in pairwise(closes)))

    states = jsonl(folder / "btc-volatility.jsonl")
    proposals = jsonl(folder / "stake-decisions.jsonl")
    observed = {}
    by_day = {}
    attempted = Counter()
    for state, order in zip(states, proposals, strict=True):
        if (state["time"], state["pair"]) != (order["time"], order["pair"]):
            raise ValueError("Signal and sizing clocks differ")
        clock = pd.Timestamp(order["time"]).tz_convert("UTC")
        cutoff = clock.floor("D") - pd.Timedelta(days=1)
        if state["last_closed_day"] != cutoff.isoformat():
            raise ValueError("Unexpected BTC daily cutoff")
        if cutoff not in by_day:
            history = [norm(cutoff - pd.Timedelta(days=i)) for i in range(7, 97)]
            by_day[cutoff] = (norm(cutoff), float(np.median(history)))
        current, reference = by_day[cutoff]
        for name, value in (("volatility", current), ("reference", reference)):
            actual = state[name]
            if math.isfinite(value) and value > 0:
                if actual is None or not math.isclose(value, actual, abs_tol=1e-12):
                    raise ValueError(f"Independent {name} calculation disagrees")
            elif actual is not None:
                raise ValueError(f"Invalid {name} was admitted")
        ratio = 1.75 if 0 < current <= reference else 1.0
        stake = min(order["max_stake"], max(order["min_stake"] or 0, order["native_stake"] * ratio))
        if ratio != order["multiplier"] or not math.isclose(
            stake, order["requested_stake"], abs_tol=1e-9
        ):
            raise ValueError("Audited proposal differs")
        key = (order["pair"], clock.value // 1_000_000, order["tag"].strip())
        observed[key] = ratio
        attempted[str(ratio)] += 1
    filled = Counter(
        str(observed[(t["pair"], t["open_timestamp"], t["enter_tag"].strip())])
        for t in trades
        if scope(t)
    )
    return {
        "matched_filled_top_coin_campaigns": sum(filled.values()),
        "filled_multipliers": dict(filled),
        "attempted_multipliers": dict(attempted),
        "decision_days": len(by_day),
        "btc_source_sha256": receipts[BTC_DAILY],
        "btc_volatility_trace_sha256": sha(folder / "btc-volatility.jsonl"),
        "sizing_trace_sha256": sha(folder / "stake-decisions.jsonl"),
    }


def account(plan, window, cost):
    folder = OUT / f"{window}_{cost}bps"
    spec = read(plan)
    (candidate,) = spec["candidates"]
    with folder.with_suffix(".log").open("x") as log:
        subprocess.run(
            [
                sys.executable,
                "-B",
                str(PARENT / "run_wide_comparison.py"),
                "--plan",
                str(plan),
                "--candidate",
                STRATEGY,
                "--cost-bps",
                str(cost),
                "--output",
                str(folder),
            ],
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    summary = temporal.audit_one(spec, candidate, cost, folder)
    trades, _ = native_trades(spec, folder, STRATEGY, cost)
    summary.update(audit_volatility(spec, folder, trades))
    save(folder / "audit.json", summary)
    cache = folder / "lossless_cache"
    if cache.is_dir() and not cache.is_symlink():
        shutil.rmtree(cache)
    print(
        window,
        cost,
        summary["periods"]["continuous"]["net_return"],
        summary["filled_multipliers"],
        flush=True,
    )
    return summary


def main():
    if sys.argv[1] == "register":
        register()
        return
    if sys.argv[1] != "temporal":
        raise SystemExit("Use register or temporal")
    for source in read(REGISTRATION)["sources"]:
        checked(source)
    OUT.mkdir()
    receipts = {str(cost): account(SHORT_PLAN, "temporal", cost) for cost in (15, 8)}
    save(OUT / "temporal_audit.json", receipts)


if __name__ == "__main__":
    main()
