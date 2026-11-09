"""Admit inputs for the already frozen100USDT spot contract."""

import copy
from datetime import UTC, datetime
from pathlib import Path

from research.crypto.nfi_blacklist_rules_20261109.inputs import read, ref, save


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = ROOT / "user_data/crypto_quant/nfi_truth_audit_20261109_v001"


def main():
    if (HERE / "spot-registration.json").exists():
        raise FileExistsError("Preserve frozen spot registration")
    frozen = read(HERE / "economic-registration.json")["additional_spot_case_frozen_contract"]
    admission = read(HERE / "spot-input-admission.json")
    if admission["pairs"] != len(frozen["pairs"]) or admission["input_files"] != 180:
        raise ValueError("Missing spot assets or timeframes")
    plan = copy.deepcopy(read(HERE / "author100-plan.json"))
    config = read(ROOT / "research/crypto/spot_baseline_20261105/nfi-x8-spot.json")
    config.update(
        dry_run_wallet=100,
        available_capital=99,
        capital_ceiling_usdt=100,
        bot_name="spot100",
        strategy="FlatTopBoost",
        max_open_trades=6,
    )
    config["exchange"].update(key="", secret="", password="", pair_whitelist=frozen["pairs"])
    config["api_server"] = {"enabled": False}
    config["telegram"] = {"enabled": False}
    config.pop("webhook", None)
    save(HERE / "spot100-config.json", config)
    save(HERE / "spot-static-schedule.json", {"days": {}, "pairs": frozen["pairs"]})
    candidate = plan["candidates"][0]
    candidate.update(name="spot100", config=ref(HERE / "spot100-config.json"), max_open_trades=6)
    plan.update(
        registered_at=datetime.now(UTC).isoformat(),
        pairs=frozen["pairs"],
        allowed_leverage=1,
        datadir=str(OUT / "spot-candles-v002"),
        total_cost_bps_per_side=[13],
        dynamic_universe=False,
        marks={},
        data_receipts=ref(OUT / "spot-data-receipts.json"),
        historical_schedule=ref(HERE / "spot-static-schedule.json"),
        market_snapshot=ref(
            ROOT / "research/crypto/spot_baseline_20261105/ccxt-spot-market-rules.json"
        ),
        historical_name_blacklist_bias=False,
        cost_model="10bps spot commission plus3bps execution proxy per side; no funding",
        comparison_purpose="Native fixed100USDT spot-cap reproduction, static36coins",
        known_limitations=[
            "Static current36spot assets, not point-in-time membership",
            "Current metadata, not historical exchange rule proof",
            "Five-minute OPEN fill/valuation model, no orderbook simulation",
            "No independent OOS; inspected related outcomes are known",
        ],
        spot_input_admission=ref(HERE / "spot-input-admission.json"),
    )
    save(HERE / "spot100-plan.json", plan)
    save(
        HERE / "spot-registration.json",
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "parent_frozen_contract": ref(HERE / "economic-registration.json"),
            "plans": [{"name": "spot100", "cost": 13, "plan": ref(HERE / "spot100-plan.json")}],
            **{
                key: read(HERE / "economic-registration.json")[key]
                for key in ("runner", "capital", "observer", "protocol")
            },
            "new_spot_outcomes_known": False,
            "orders_enabled": False,
        },
    )
    print({"spot_account_registered": True, "assets": 36, "capital": 100, "slots": 6})


if __name__ == "__main__":
    main()
