"""Freeze100USDT native accounts; no thresholds fitted to the new outcomes."""

import copy
import shutil
from datetime import UTC, datetime
from pathlib import Path

import rapidjson

from research.crypto.nfi_blacklist_rules_20261109.inputs import read, ref, save, sha


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = ROOT / "user_data/crypto_quant/nfi_truth_audit_20261109_v001"
PARENT = ROOT / "research/crypto/nfi_blacklist_rules_20261109"


def main():
    if (HERE / "economic-registration.json").exists():
        raise FileExistsError("Preserve economic registration")
    parent = read(PARENT / "structural-plan.json")
    shutil.copy2(ROOT / "deploy/mainnet_100_20261109/capital.py", HERE / "capital_snapshot.py")
    payload = read(parent["historical_schedule"]["path"])
    previous = read(
        ROOT / "user_data/crypto_quant/nfi80_historical_universe_20261109_v001/schedule.json"
    )
    blacklist_source = read(PARENT / "eligibility.json")["old_blacklist"]
    rules = rapidjson.loads(Path(blacklist_source["path"]).read_text(), parse_mode=3)["exchange"][
        "pair_blacklist"
    ]
    author = {
        **payload,
        "structural_blacklist": rules,
        "days": previous["days"],
        "union": previous["union"],
        "historical_blacklist_hindsight_bias": True,
    }
    save(HERE / "author-schedule.json", author)
    plans = []
    for name, schedule in (
        ("author100", HERE / "author-schedule.json"),
        ("structural100", Path(parent["historical_schedule"]["path"])),
    ):
        plan = copy.deepcopy(parent)
        pairs = read(schedule)["union"]
        candidate = plan["candidates"][0]
        config = read(candidate["config"]["path"])
        config.update(
            dry_run_wallet=100, available_capital=99, capital_ceiling_usdt=100, bot_name=name
        )
        config["exchange"].update(pair_whitelist=pairs)
        config["pairlists"] = [
            {
                "method": "HistoricalVolumePairList",
                "schedule": str(schedule),
                "sha256": sha(schedule),
            }
        ]
        if name == "author100":
            config["exchange"]["pair_blacklist"] = rules
        config_path = HERE / f"{name}-config.json"
        save(config_path, config)
        candidate.update(name=name, config=ref(config_path))
        plan.update(
            schema="freqtrade.research.truth-audit-cap100.v1",
            registered_at=datetime.now(UTC).isoformat(),
            initial_equity_usdt=100,
            pairs=pairs,
            historical_schedule=ref(schedule),
            runner_sha256=sha(HERE / "native_cap_runner.py"),
            observer_source=ref(HERE / "observer.py"),
            capital_source=ref(HERE / "capital_snapshot.py"),
            total_cost_bps_per_side=[8],
            maximum_financial_workers=1,
            memory_guard_bytes=int(5.5 * 1024**3),
            wall_guard_seconds=7200,
            known_new_account_returns=False,
            prior1000USDT_returns_known=True,
            marks={
                p: ref(
                    Path(parent["datadir"])
                    / "futures"
                    / (p.replace("/", "_").replace(":", "_") + "-1h-mark.feather")
                )
                for p in pairs
            },
            historical_name_blacklist_bias=name == "author100",
            complete_point_in_time_market_rules=False,
            independent_oos=False,
            risk_gate=0.30,
            economic_protocol=ref(HERE / "PROTOCOL.md"),
        )
        path = HERE / f"{name}-plan.json"
        save(path, plan)
        plans.append({"name": name, "cost": 8, "plan": ref(path)})
    save(
        HERE / "economic-registration.json",
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "plans": plans,
            "runner": ref(HERE / "native_cap_runner.py"),
            "capital": ref(HERE / "capital_snapshot.py"),
            "observer": ref(HERE / "observer.py"),
            "protocol": ref(HERE / "PROTOCOL.md"),
            "additional_spot_case_frozen_contract": {
                "market": "spot",
                "capital": 100,
                "stake_budget": 99,
                "leverage": 1,
                "slots": 6,
                "cost_bps": 13,
                "pairs": read(ROOT / "deploy/configs/spot.json")["exchange"]["pair_whitelist"],
                "start": parent["start"],
                "end": parent["end"],
                "comparison_end": parent["comparison_end"],
                "new_data_admission_required": True,
                "do_not_drop_missing_assets": True,
            },
            "orders_enabled": False,
        },
    )
    print(
        {
            "futures_accounts_registered": len(plans),
            "capital_each": 100,
            "fee_bps": 8,
            "spot_inputs_pending": True,
        }
    )


if __name__ == "__main__":
    main()
