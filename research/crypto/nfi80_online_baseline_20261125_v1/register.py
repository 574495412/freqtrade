"""Bind the production-style historical candidate without loading live configuration."""

from datetime import datetime, timezone
from pathlib import Path

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt, save


HERE = Path(__file__).resolve().parent
NAME = "FlatBoostFourteenSlots"


def register(study, strategy, slots, behavior, evidence=()):
    path = study / "plan.json"
    if path.exists():
        raise FileExistsError(path)
    alignment = HERE / "baseline_alignment.json"
    history = read(alignment)["native_historical_reference"]
    parent_path = checked(history["plan"])
    plan = read(parent_path)
    control, = plan["candidates"]
    assert control["strategy"] == "FlatTopBoost" and control["max_open_trades"] == 12
    config = read(checked(control["config"]))
    assert config["dry_run"] and config["margin_mode"] == "isolated"
    assert config["stake_amount"] == "unlimited" and config["max_open_trades"] == 12
    assert plan["allowed_leverage"] == 3 and plan["initial_equity_usdt"] == 1000
    assert [row["method"] for row in config["pairlists"]] == ["HistoricalVolumePairList"]
    candidate_path = study / "config.json"
    save(candidate_path, {**config, "strategy": strategy, "max_open_trades": slots})
    strategy_file = f"{strategy}.py"
    candidate = {
        **control, "name": strategy, "strategy": strategy, "max_open_trades": slots,
        "config": receipt(candidate_path),
        "source_files": {**control["source_files"], strategy_file: receipt(study / strategy_file)},
        "source_and_risk_behavior": behavior,
    }
    save(path, {
        **plan, "candidates": [candidate], "predecessor_plan": receipt(parent_path),
        "protocol_receipt": receipt(study / "protocol.json"), "online_alignment": receipt(alignment),
        "mechanism_evidence": list(map(receipt, evidence)),
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "comparison_purpose": "Production-style historical replay against archived daily twelve-slot FlatTopBoost.",
        "orders_enabled": False, "production_eligible": False,
    })


if __name__ == "__main__":
    register(HERE, NAME, 14, "FlatTopBoost with fourteen slots and the original twelve-slot stake proposal.")
