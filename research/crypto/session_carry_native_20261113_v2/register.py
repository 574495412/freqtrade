"""Register supported native carry policies after the rejected rollover study."""

import json
from datetime import UTC, datetime
from pathlib import Path

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD = ROOT / "research/crypto/session_reversal_native_20261113_v1"
OUT = ROOT / "user_data/crypto_quant/session_carry_native_20261113_v2"


def ref(path):
    return {"path": str(path), "sha256": sha(path)}


def main():
    if (HERE / "plan.json").exists():
        raise FileExistsError("Frozen plan exists")
    old = json.loads((OLD / "plan.json").read_text())
    if (
        sha(HERE / "SessionRules.py")
        != old["candidates"][0]["source_files"]["SessionRules.py"]["sha256"]
    ):
        raise ValueError("Legacy financial strategy changed")
    OUT.mkdir()
    protocol = {
        "registered_at": datetime.now(UTC).isoformat(),
        "source_commit": "5693993e108f2b3668bd21b1dd36ae31f79d8fcd",
        "selection_context": "Prior forced-rollover losses are known. This is an execution-policy follow-up of the same family, not independent alpha discovery or OOS.",
        "fixed_direction_rules": "BTC08/20UTC reversal of previous SAME session; ETH05 reversal,17 long. No source hour/sign/holding horizon/fee/leverage retune.",
        "boundary_carry": "Rows ending at boundary T select directions from actual T-24h/T-12h quotes. Native exit/entry flags close opposite side then reverse at native T open; same side carries actual quantity with no orders.",
        "clock_matched_carry": "Same direction-change exits at T and entries at T+5min as original rollover. Same-direction positions persist. Isolates carry from moving all flips to boundary.",
        "allocation_difference_from_paper": "At entry use native99% available cash, lot-rounded. Hold actual quantity until direction change, no free per-session notional reset. Short gross/equity may drift above1 while native leverage remains1. Report actual exposure.",
        "controls": "BoundaryCarryLong uses same boundary/support,always long. Four15bps original SourceSessions/PassiveLong native accounts rerun with original byte-identical strategy; require full trade and hourlyNAV parity.",
        "window": [old["start"], old["comparison_end"]],
        "capital": "Each BTC/ETH wallet500; sum actual500+500=1000, no transfers/rebalancing/scaled1000 paths",
        "cost_bps_per_side": [15, 8],
        "acceptance": "Primary boundary-carry source positive full and both continuous annual slices, hourlyDD<=50%, above matched boundary-long and old passive two-wallet combos, at BOTH costs. Clock-matched model attribution only; never promote the better timing after seeing outcomes. Numeric pass alone is not independent qualification.",
        "native_contract": "Same pinned Backtesting/markets/1x isolated/5m/catastrophic99%stop/liquidation/fees/funding/observer; no core financial engine change",
        "orders_enabled": False,
        "production_eligible": False,
    }
    save(HERE / "protocol.json", protocol)
    candidates = []
    for strategy in [
        "BoundaryCarrySource",
        "ClockMatchedCarrySource",
        "BoundaryCarryLong",
        "SourceSessions",
        "PassiveLong",
    ]:
        for sym in ["BTC", "ETH"]:
            parent = next(c for c in old["candidates"] if c["name"] == f"SourceSessions_{sym}")
            pair = parent["pairs"]
            config = json.loads(Path(parent["config"]["path"]).read_text())
            config["strategy"] = strategy
            name = f"{strategy}_{sym}"
            path = HERE / (name + ".json")
            save(path, config)
            candidates.append(
                {
                    "name": name,
                    "strategy": strategy,
                    "capital": 500,
                    "pairs": pair,
                    "costs": [15] if strategy in ["SourceSessions", "PassiveLong"] else [15, 8],
                    "config": ref(path),
                    "source_files": {
                        n: ref(HERE / n) for n in ["SessionRules.py", "CarryRules.py"]
                    },
                    "role": "default_off_parity"
                    if strategy in ["SourceSessions", "PassiveLong"]
                    else "new_funded_policy",
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
    plan = {k: old[k] for k in keys}
    plan.update(
        schema="freqtrade.native.session-carry.v2",
        registered_at=protocol["registered_at"],
        output_root=str(OUT),
        candidates=candidates,
        initial_equity_usdt=500,
        total_cost_bps_per_side=[15, 8],
        runner_sha256=sha(HERE / "native_runner.py"),
        runner_module="research.crypto.session_carry_native_20261113_v2.native_runner",
        additional_source_seals=[
            ref(p)
            for p in [
                HERE / "protocol.json",
                HERE / "source_manifest.json",
                HERE / "run.py",
                HERE / "preflight.py",
                OLD / "plan.json",
                OLD / "funding_audit.json",
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
                "accounts_registered": sum(len(c["costs"]) for c in candidates),
                "past_rollover_results_known": True,
                "new_policy_outcomes_seen": False,
            }
        )
    )


if __name__ == "__main__":
    main()
