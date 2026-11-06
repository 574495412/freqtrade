"""Freeze leverage and direction ablations with the unchanged native engine."""

import math
import shutil
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from research.crypto.nfi_profit_ramp_20261107 import study as a13
from research.crypto.nfi_solo_allocation_20261107 import long_mark
from research.crypto.nfi_solo_allocation_20261107.run import jsonl
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation, temporal
from research.crypto.nfi_strength_allocation_20261107.audit import native_trades, read
from research.crypto.nfi_strength_allocation_20261107.register import DATA, PARENT, record
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked, save, sha


HERE = Path(__file__).resolve().parent
OUT = DATA / "nfi_btc_leverage_20261107_v001"
PROTOCOL = HERE / "registration.json"
RESULT = HERE / "result.json"
A04 = a13.A04
COSTS = (15, 8)
CASES = {"Native2x": 2.0, "Direction1x": 1.0, "Direction2x": 2.0}
PARENTS = {"temporal": A04 / "temporal_registration.json", "2024": a13.PLAN}
BTC = "futures/BTC_USDT_USDT-1d-futures.feather"


def register():
    if PROTOCOL.exists() or OUT.exists():
        raise FileExistsError("Leverage experiment already registered")
    template = read(PARENTS["temporal"])["candidates"][0]
    native = read(checked(template["config"]))
    if native["trading_mode"] != "futures" or native["margin_mode"] != "isolated":
        raise ValueError("Experiment requires isolated futures")
    if native["nfi_parameters"] != {
        "futures_mode_leverage": 1.0,
        "futures_mode_leverage_rebuy_mode": 1.0,
        "futures_mode_leverage_grind_mode": 1.0,
    }:
        raise ValueError("Baseline leverage overrides differ")
    plans = {}
    configs = []
    for name, leverage in CASES.items():
        cfg = HERE / f"{name}.json"
        params = dict.fromkeys(native["nfi_parameters"], leverage)
        strategy = "BtcDirection" if name.startswith("Direction") else template["strategy"]
        save(cfg, {**native, "nfi_parameters": params, "strategy": strategy})
        configs.append(cfg)
        source = {**template["source_files"]}
        if name.startswith("Direction"):
            source["BtcDirection.py"] = record(HERE / "BtcDirection.py")
        candidate = {
            **template,
            "name": name,
            "strategy": strategy,
            "config": record(cfg),
            "source_files": source,
            "source_and_risk_behavior": "Isolated leverage and BTC direction ablation.",
        }
        for stage, old in PARENTS.items():
            parent = read(old)
            parent.pop("baselines", None)
            path = HERE / f"native_{stage}_{name}.json"
            save(
                path,
                {
                    **parent,
                    "schema": "freqtrade.research.nfi-btc-leverage.v1",
                    "registered_at": datetime.now(UTC).isoformat(),
                    "candidates": [candidate],
                    "total_cost_bps_per_side": list(COSTS),
                    "allowed_leverage": leverage,
                    "orders_enabled": False,
                },
            )
            plans[f"{stage}_{name}"] = record(path)
    sources = (
        Path(__file__),
        HERE / "BtcDirection.py",
        HERE / "evaluate.py",
        *configs,
        *PARENTS.values(),
        A04 / "temporal_result.json",
        A04 / "mark_result.json",
        a13.RESULT,
        a13.OUT / "5m/admission.json",
        Path(long_mark.__file__),
        Path(mark_valuation.__file__),
        Path(temporal.__file__),
        mark_valuation.ORACLE,
        A04 / "audit.py",
    )
    save(
        PROTOCOL,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "sources": [record(p) for p in sources],
            "plans": plans,
            "rule": (
                "Same native X8, 40 USD-M pairs, six slots, 1000 USDT and isolated margin. "
                "Native2x only changes the three native leverage config fields from 1 to 2. "
                "Direction1x and Direction2x retain the same respective leverage and veto "
                "otherwise accepted long entries if BTC's prior UTC daily close is below its "
                "30-day mean (inclusive of that close), and short entries if at or above. "
                "Require all 30 exact closes; gaps or invalid prices block entry. No changes "
                "to margin stake parameters, exits, or adjustments. Leverage can still alter "
                "native callback profits and subsequent path. No extra halt overlay."
            ),
            "gate": (
                "Run all three variants at both costs in 65 days, reusing original 1x controls. "
                "For Native2x progression require both costs with no native liquidation, "
                "at least 12 complete closes, full net return > original +1pp, "
                "full net return/5m drawdown >= original, and 5m drawdown <=25%. "
                "For Direction2x additionally require return/drawdown > Native2x and "
                "Direction1x, full net return >=80% of Native2x, and return > Direction1x. "
                "Only 2x variants passing both costs get 2024 confirmatory accounts; 2024 "
                "requires the same absolute risk, >original in both halves and at least "
                "60 natural closes, with no liquidation. If Direction2x qualifies also run "
                "matched 1x direction and uniform 2x controls there. Record all outcomes. "
                "These are aggressive research thresholds, not historical risk certification."
            ),
            "liquidation_limit": (
                "Count native liquidation exits and preserve modeled trade-level buffered "
                "levels. Freqtrade's native modeled outcomes and 5m marked NAV do not "
                "establish actual intrabar liquidation or exchange liquidation fee parity."
            ),
            "known_history": (
                "Prior 1x controls in both windows were known. All 1x-direction and 2x results "
                "under this protocol were unknown at registration. Both windows are retrospective."
            ),
            "orders_enabled": False,
        },
    )


def check():
    policy = read(PROTOCOL)
    for item in policy["sources"]:
        checked(item)
    return {key: checked(ref) for key, ref in policy["plans"].items()}


def audit_direction(plan, path, trades):
    rows = jsonl(path / "btc-direction.jsonl")
    receipt = read(checked(plan["data_receipts"]))
    data = checked({"path": str(Path(plan["datadir"]) / BTC), "sha256": receipt[BTC]})
    series = pd.read_feather(data).set_index("date").close.to_dict()
    admitted = set()
    count = Counter()
    for r in rows:
        at = pd.Timestamp(r["time"])
        day = at.tz_convert("UTC").floor("D") - pd.Timedelta(days=1)
        close = [series.get(day - pd.Timedelta(days=i), math.nan) for i in range(30)]
        mean = (
            math.fsum(close) / len(close)
            if all(math.isfinite(p) and p > 0 for p in close)
            else None
        )
        passed = mean is not None and (
            (r["side"] == "long" and close[0] >= mean) or (r["side"] == "short" and close[0] < mean)
        )
        if (
            day.isoformat() != r["last_daily"]
            or passed != r["accepted"]
            or (mean is not None and not math.isclose(mean, r["sma_30"], abs_tol=1e-10))
        ):
            raise ValueError("Independent BTC direction gate differs")
        count[f"{r['side']}_{'admitted' if passed else 'blocked'}"] += 1
        if passed:
            admitted.add((r["pair"], at.value // 1_000_000, r["side"]))
    if any(
        (t["pair"], t["open_timestamp"], "short" if t["is_short"] else "long") not in admitted
        for t in trades
    ):
        raise ValueError("Native entry bypassed BTC direction gate")
    return {
        "direction_decisions": dict(count),
        "direction_trace_sha256": sha(path / "btc-direction.jsonl"),
    }


def account(plan_path, stage, name, cost):
    plan = read(plan_path)
    (case,) = plan["candidates"]
    key = f"{stage}_{name}_{cost}bps"
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
                name,
                "--cost-bps",
                str(cost),
                "--output",
                str(folder),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    summary = temporal.audit_one(plan, case, cost, folder)
    trades, _ = native_trades(plan, folder, case["strategy"], cost)
    if name.startswith("Direction"):
        summary.update(audit_direction(plan, folder, trades))
    raw = read(folder / "effective_config.json")
    fills = [o for t in trades for o in t["orders"] if o["amount"] > 0]
    summary.update(
        {
            "leverage": plan["allowed_leverage"],
            "margin_mode": raw["margin_mode"],
            "entry_notional_usdt": math.fsum(
                o["amount"] * o["safe_price"] for o in fills if o["ft_is_entry"]
            ),
            "exit_notional_usdt": math.fsum(
                o["amount"] * o["safe_price"] for o in fills if not o["ft_is_entry"]
            ),
            "native_model_funding_usdt": math.fsum(t["funding_fees"] or 0 for t in trades),
            "native_liquidations": sum(t["exit_reason"] == "liquidation" for t in trades),
            "reported_liquidation_levels": sum(
                t.get("liquidation_price") is not None for t in trades
            ),
            "longs": sum(not t["is_short"] for t in trades),
            "shorts": sum(t["is_short"] for t in trades),
        }
    )
    save(folder / "audit.json", summary)
    cache = folder / "lossless_cache"
    if cache.is_dir() and not cache.is_symlink():
        shutil.rmtree(cache)
    print(
        key,
        summary["periods"]["continuous"]["net_return"],
        summary["native_liquidations"],
        flush=True,
    )
    return key, summary


if __name__ == "__main__":
    if sys.argv[1:] != ["register"]:
        raise SystemExit("Use register")
    register()
