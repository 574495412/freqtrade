"""Freeze native configs/plans before the new rule-account economics."""

from datetime import UTC, datetime

from research.crypto.nfi_blacklist_rules_20261109.inputs import (
    HERE,
    OUT,
    PLAN,
    ROOT,
    candidates,
    read,
    ref,
    save,
    sha,
    structural_rules,
)


def main():
    if (HERE / "plans.json").exists():
        raise FileExistsError("Preserve registered policies")
    admission = read(OUT / "admission.json")
    if set(admission) != set(candidates()):
        raise ValueError("Missing selected-universe data admission")
    summaries = read(HERE / "schedule-summary.json")
    parent_path = (
        ROOT / "research/crypto/nfi80_historical_universe_20261109/v003/flatboost-plan.json"
    )
    parent = read(parent_path)
    markets = read(PLAN["original_full_market_snapshot"]["path"])
    eligible = candidates()
    markets.update(
        markets={p: markets["markets"][p] for p in eligible},
        leverage_tiers={p: markets["leverage_tiers"][p] for p in eligible},
    )
    save(OUT / "market-rules.json", markets)
    plans = {}
    for name in ["parity", "structural", "native_risk"]:
        if name != "parity" and not summaries[name]["contract_admitted"]:
            continue
        plan = (
            read(
                ROOT
                / "research/crypto/nfi80_historical_universe_20261109/parity-technical-plan.json"
            )
            if name == "parity"
            else read(parent_path)
        )
        candidate = plan["candidates"][0]
        config = read(candidate["config"]["path"])
        pairs = (
            plan["pairs"]
            if name == "parity"
            else read(summaries[name]["schedule"]["path"])["union"]
        )
        schedule = summaries["structural" if name == "parity" else name]["schedule"]
        if name != "parity":
            config["exchange"].update(pair_whitelist=pairs, pair_blacklist=structural_rules())
            config["enable_dynamic_pairlist"] = True
            config["pairlists"] = [
                {
                    "method": "HistoricalVolumePairList",
                    "schedule": schedule["path"],
                    "sha256": schedule["sha256"],
                }
            ]
            config["bot_name"] = "blacklist-policy-" + name
        save(HERE / (name + "-config.json"), config)
        candidate.update(name=name, config=ref(HERE / (name + "-config.json")))
        plan.update(
            schema="freqtrade.research.blacklist-policy.v1",
            registered_at=datetime.now(UTC).isoformat(),
            engine_source=str(OUT / "engine"),
            engine_manifest=ref(HERE / "engine-manifest.json"),
            extension_patch=ref(HERE / "native.patch"),
            historical_schedule=schedule,
            runner_sha256=sha(HERE / "native_runner.py"),
            storage_source=ref(HERE / "storage.py"),
            pairs=pairs,
            datadir=str(OUT / "candles"),
            data_receipts=ref(OUT / "data-receipts.json"),
            market_snapshot=ref(OUT / "market-rules.json"),
            marks={
                p: ref(
                    OUT
                    / "candles/futures"
                    / (p.replace("/", "_").replace(":", "_") + "-1h-mark.feather")
                )
                for p in pairs
            },
            research_drawdown_limit=0.30,
            new_policy_economic_results_known=False,
            prior_related_results_known=True,
            policy_protocol=ref(HERE / "PROTOCOL.md"),
            dynamic_universe=name != "parity",
            parent_plan=ref(parent_path),
            selection_protocol=ref(HERE / "PROTOCOL.md"),
            orders_enabled=False,
            production_eligible=False,
            known_limitations=parent["known_limitations"]
            + [
                "Current author set and market/tier metadata still not full PIT universe",
                "Native historical risk-filter bridge is a local extension; not upstream backtest support",
                "Late-listing warmup uses actual contract onboard dates; prelisting placeholders excluded",
            ],
        )
        save(HERE / (name + "-plan.json"), plan)
        plans[name] = ref(HERE / (name + "-plan.json"))
    save(
        HERE / "plans.json",
        {
            "plans": plans,
            "costs": [15, 8],
            "sources": {p.name: ref(p) for p in HERE.glob("*.py")},
            "runner": ref(HERE / "native_runner.py"),
            "protocol": ref(HERE / "PROTOCOL.md"),
            "created_at_utc": datetime.now(UTC).isoformat(),
            "financial_workers": 1,
            "orders_enabled": False,
        },
    )
    print({"registered": list(plans), "new_policy_accounts": 2 * (len(plans) - 1)}, flush=True)


if __name__ == "__main__":
    main()
