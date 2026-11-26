"""Freeze two nominal funding-order scenarios and native default controls."""

import json
from datetime import UTC, datetime
from pathlib import Path

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD = HERE.parent / "session_carry_native_20261113_v2"
OUT = ROOT / "user_data/crypto_quant/session_funding_native_20261114_v3"


def ref(path):
    return {"path": str(path), "sha256": sha(path)}


def main():
    if (HERE / "plan.json").exists():
        raise FileExistsError("Preserve frozen plan")
    parent = json.loads((OLD / "plan.json").read_text())
    for name in ["CarryRules.py", "SessionRules.py"]:
        if sha(HERE / name) != parent["candidates"][0]["source_files"][name]["sha256"]:
            raise ValueError("Frozen strategy drift")
    OUT.mkdir()
    protocol = {
        "registered_at": datetime.now(UTC).isoformat(),
        "known_results": "V2 inclusive funding policy has8bps+6.03% and15bps-51.63%,fails2026 andpressure. No independent new alpha or source/hour/weight retune.",
        "native_owner": "Local subclass of fixed native Backtesting overrides only funding callbacks/order-funding transfer; original matcher,wallet,strategy,quantities,fees,risk remain native. Private-method local extension,not an upstream supported stable plugin.",
        "before_orders": "At nominal eventT,the position immediately before native boundary orders owns it:open<T<=close",
        "after_orders": "Position after native boundary fills owns eventT:open<=T<close. Existing continuing positions accrue current events normally; final closing amount is set before native order transfer.",
        "scope": "Single full entry/full exit positions,no DCA/partial fills/position adjustments. Same pinned BoundaryCarrySource and BoundaryCarryLong. Nominal-clock assumptions,not certified actual settlement or rigorous real-return bounds.",
        "full_replay": "All orders,sizes,funding,cash,subsequent lot rounding andNAV rerun through native chain. Not adding fixed-fill cash deltas to oldNAV.",
        "matrix": "16actual scenario accounts(twoownership*two policies*twoassets*twocosts)plus4inclusive15bps technical reruns.500USDT each;actualBTC500+ETH500sum1000;no transfers.",
        "source_selection_limits": "Same author5693993e108f2b3668bd21b1dd36ae31f79d8fcd;original full-sample selection and native local market/quantity differences retained.",
        "window": [parent["start"], parent["comparison_end"]],
        "acceptance": "Source positive full andboth continuous annual slices,DD<=50%,above matchedlong andoldpassive atboth costs inBOTH ownership scenarios;no selecting favorable funding order after outcomes.",
        "exchange_limit": "Binance public FAQ allows processing deviation around nominaltime. Neither nominalorder case nor publiccalc_time proves private account attribution.",
        "orders_enabled": False,
        "production_eligible": False,
    }
    save(HERE / "protocol.json", protocol)
    candidates = []
    for mode in ["inclusive", "before_orders", "after_orders"]:
        for strategy in ["BoundaryCarrySource", "BoundaryCarryLong"]:
            for sym in ["BTC", "ETH"]:
                original = next(c for c in parent["candidates"] if c["name"] == f"{strategy}_{sym}")
                name = f"{strategy}_{sym}_{mode}"
                config = json.loads(Path(original["config"]["path"]).read_text())
                path = HERE / (name + ".json")
                save(path, config)
                candidates.append(
                    {
                        **{k: original[k] for k in ["strategy", "capital", "pairs"]},
                        "name": name,
                        "costs": [15] if mode == "inclusive" else [15, 8],
                        "funding_ownership": mode,
                        "config": ref(path),
                        "source_files": {
                            n: ref(HERE / n) for n in ["SessionRules.py", "CarryRules.py"]
                        },
                        "role": "default_off_parity"
                        if mode == "inclusive"
                        else "full_ownership_scenario",
                        "original_candidate": original["name"],
                    }
                )
    keys = [
        "engine_source",
        "engine_manifest",
        "engine_commit",
        "environment",
        "market_snapshot",
        "research_support",
        "storage_source",
        "observer_source",
        "start",
        "end",
        "comparison_end",
        "periods",
        "datadir",
        "data_receipts",
        "pairs",
        "marks",
        "allowed_leverage",
        "memory_guard_bytes",
        "wall_guard_seconds",
    ]
    plan = {k: parent[k] for k in keys}
    plan.update(
        schema="freqtrade.native.funding-ownership.v3",
        registered_at=protocol["registered_at"],
        output_root=str(OUT),
        candidates=candidates,
        initial_equity_usdt=500,
        total_cost_bps_per_side=[15, 8],
        runner_sha256=sha(HERE / "native_runner.py"),
        runner_module="research.crypto.session_funding_native_20261114_v3.native_runner",
        funding_extension=ref(HERE / "FundingOwnershipBacktesting.py"),
        additional_source_seals=[
            ref(p)
            for p in [
                HERE / "protocol.json",
                HERE / "source_manifest.json",
                HERE / "run.py",
                HERE / "preflight.py",
                HERE / "FundingOwnershipBacktesting.py",
                HERE / "test_funding_ownership.py",
                OLD / "plan.json",
                OLD / "preflight.json",
                OLD / "preflight.py",
                OLD / "funding_recheck.json",
                ROOT / "research/crypto/band_trend_native_20261113_v1/observe_clock.py",
                ROOT / "research/crypto/skew_vol_native_20261113_v1/clock_fix.py",
                *sorted((HERE / "source").glob("*")),
            ]
        ],
        orders_enabled=False,
        production_eligible=False,
    )
    save(HERE / "plan.json", plan)
    print(
        json.dumps(
            {
                "registered_accounts": sum(len(c["costs"]) for c in candidates),
                "new_ownership_outcomes_seen": False,
            }
        )
    )


if __name__ == "__main__":
    main()
