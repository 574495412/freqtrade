"""Register and build an exact public funding input for short-rebuy sizing."""

import math
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.listing_reversal_20261107 import catalog
from research.crypto.nfi_profit_ramp_20261107 import study as a13
from research.crypto.nfi_residual_recovery_20261107 import study as a15
from research.crypto.nfi_solo_allocation_20261107 import long_mark
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation, temporal
from research.crypto.nfi_strength_allocation_20261107.audit import read
from research.crypto.nfi_strength_allocation_20261107.register import DATA, record
from research.crypto.spot_perp_flow_gap_20261107 import inputs as flow
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked, save, sha


HERE = Path(__file__).resolve().parent
OUT = DATA / "nfi_funding_rebuy_20261107_v001"
REGISTRATION = HERE / "registration.json"
RESULT = HERE / "result.json"
A04 = a13.A04
PARENTS = a15.PARENTS
COSTS = (15, 8)
CASES = ("FlatRebuyBoost", "FundingRebuyBoost")
TAGS = {"561", "562", "563"}


def scoped(t):
    tags = set((t["enter_tag"] or "").split())
    return t["is_short"] and bool(tags) and tags <= TAGS


def register():
    if REGISTRATION.exists() or OUT.exists():
        raise FileExistsError("Study already registered")
    a15_result = a15.checked_protocol()
    sources = (
        Path(__file__),
        HERE / "RebuyBoost.py",
        HERE / "evaluate.py",
        *PARENTS.values(),
        A04 / "result.json",
        a13.RESULT,
        A04 / "temporal_result.json",
        A04 / "mark_result.json",
        long_mark.study.HERE / "long_mark_result.json",
        long_mark.OUT / "admission.json",
        Path(temporal.__file__),
        Path(mark_valuation.__file__),
        mark_valuation.ORACLE,
        A04 / "audit.py",
        Path(flow.__file__),
        Path(catalog.__file__),
        Path(a15.__file__),
    )
    save(
        REGISTRATION,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "sources": [record(path) for path in sources],
            "original_archives": a15_result["original_archives"],
            "feature": (
                "Use Binance published settled USD-M funding events in (t-15min-24h, t-15min]. "
                "Require at least 3 observations with no >9h gap including boundaries, "
                "latest rate positive and exact 24h sum >=0.0003. Pure X8 short rebuy tags "
                "561, 562, 563 only. FundingRebuyBoost increases first stake to 2.5x if "
                "eligible else 1x; FlatRebuyBoost increases all scoped to 2.5x. Keep "
                "1x leverage, 40 contracts, original native allocation bounds, position "
                "adjustment and signal/exit code. Retain realized model funding in NAV."
            ),
            "hypothesis": (
                "Positive funding can identify crowded long positioning at native short entries; "
                "check directional price PnL apart from funding income and test a uniform boost."
            ),
            "attribution_gate": (
                "At each cost require at least 12 flagged natural closes, positive flagged "
                "price PnL per cumulative filled entry notional, and greater net PnL per "
                "entry notional than other pure short-rebuy trades in both 2024 and "
                "2025-Aug2026. Cohorts are based on original trades, not counterfactual NAV."
            ),
            "native_gate": (
                "Always run four native temporal accounts for 65 days (two controls at two costs, "
                "original reused). At both costs require net return above original and positive, "
                "return/5m drawdown above unconditional 2.5x, drawdown <= original +1pp, at least "
                "20 complete campaigns and 4 scoped boosted fills. Run all four 20-month accounts "
                "only after native and attribution gates pass; then require same economic gates "
                "plus net return above original in both slices, 60 complete campaigns and 12 "
                "boosted fills. Prior original return paths are known. Historical evidence only."
            ),
            "orders_enabled": False,
        },
    )


def checked_registration():
    policy = read(REGISTRATION)
    for source in policy["sources"]:
        checked(source)
    return policy


def capture():
    checked_registration()
    flow.OUT = OUT
    (OUT / "raw").mkdir(parents=True)
    plan = read(PARENTS["temporal"])
    datadir = Path(plan["datadir"])
    receipts = read(checked(plan["data_receipts"]))
    start = pd.Timestamp("2023-12-30T00:00:00Z")
    end = pd.Timestamp(plan["end"])
    first, last = (x.value // 1_000_000 for x in (start, end))

    def one(pair):
        symbol = pair.split(":")[0].replace("/", "")
        pages = {}
        source = []
        cursor = first
        while cursor < last:
            rows = flow.fetch_page(
                symbol,
                "funding",
                flow.FUNDING,
                {
                    "symbol": symbol,
                    "startTime": cursor,
                    "endTime": last - 1,
                    "limit": 1000,
                },
                pages,
            )
            if not rows:
                break
            clock = [row["fundingTime"] for row in rows]
            if clock != sorted(set(clock)) or not cursor <= clock[0] <= clock[-1] < last:
                raise ValueError(f"Funding pagination differs: {pair}")
            source.extend(rows)
            cursor = clock[-1] + 1
            if len(rows) < 1000:
                break
        if any(
            r["symbol"] != symbol
            or not math.isfinite(float(r["fundingRate"]))
            or not math.isfinite(float(r["markPrice"]))
            or float(r["markPrice"]) <= 0
            for r in source
        ):
            raise ValueError(f"Invalid official funding rows {pair}")
        clocks = [r["fundingTime"] for r in source]
        rates = [float(r["fundingRate"]) for r in source]
        name = f"futures/{pair.replace('/', '_').replace(':', '_')}-1h-funding_rate.feather"
        p = checked({"path": str(datadir / name), "sha256": receipts[name]})
        expected = pd.read_feather(p).set_index("date").funding_rate.loc[start:end]
        normalized = pd.to_datetime(clocks, unit="ms", utc=True).floor("h").as_unit("us")
        if not normalized.equals(expected.index) or not np.allclose(
            rates, expected.to_numpy(), atol=1e-13, rtol=0
        ):
            raise ValueError(f"Official history differs from model settlements: {pair}")
        return (
            pair,
            [clocks, rates],
            {"rows": len(clocks), "pages": pages, "parent": receipts[name]},
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(one, plan["pairs"]))
    tape = OUT / "settled_rates.json"
    save(tape, {p: rates for p, rates, _ in rows})
    save(
        OUT / "admission.json",
        {
            "registration_sha256": sha(REGISTRATION),
            "tape": record(tape),
            "pairs": {p: source for p, _, source in rows},
        },
    )


def bind():
    checked_registration()
    admission = read(OUT / "admission.json")
    if admission["registration_sha256"] != sha(REGISTRATION):
        raise ValueError("Tape was not captured under this registration")
    checked(admission["tape"])
    for pair in admission["pairs"].values():
        for name, receipt in pair["pages"].items():
            checked({"path": str(OUT / "raw" / name), "sha256": receipt["sha256"]})
    template = read(PARENTS["temporal"])
    base = template["candidates"][0]
    cases = []
    for strategy in CASES:
        config = HERE / f"{strategy}.json"
        native = read(checked(base["config"]))
        native["strategy"] = strategy
        if strategy == CASES[1]:
            native["research_funding_signal"] = admission["tape"]
        save(config, native)
        cases.append(
            {
                **base,
                "name": strategy,
                "strategy": strategy,
                "config": record(config),
                "source_files": {
                    **base["source_files"],
                    "RebuyBoost.py": record(HERE / "RebuyBoost.py"),
                },
            }
        )
    plans = {}
    for stage in ("temporal", "long"):
        native = read(PARENTS[stage])
        native.pop("baselines", None)
        target = HERE / f"native_{stage}.json"
        save(
            target,
            {
                **native,
                "schema": "freqtrade.research.nfi-funding-rebuy.v1",
                "candidates": cases,
                "registered_at": datetime.now(UTC).isoformat(),
                "total_cost_bps_per_side": list(COSTS),
                "orders_enabled": False,
            },
        )
        plans[stage] = record(target)
    save(
        HERE / "binding.json",
        {
            "registration_sha256": sha(REGISTRATION),
            "admission": record(OUT / "admission.json"),
            "plans": plans,
        },
    )


if __name__ == "__main__":
    {"register": register, "capture": capture, "bind": bind}[sys.argv[1]]()
