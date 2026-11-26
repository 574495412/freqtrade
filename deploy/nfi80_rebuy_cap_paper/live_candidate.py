"""Verify the highest-return candidate and write only a stopped config overlay."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from deploy.aliyun_20261111.prepare import linux_config
from deploy.mainnet_100_20261109.capital import use_available_balance
from deploy.nfi80_selected.selector import read, sha
from freqtrade.configuration.config_validation import validate_config_consistency
from freqtrade.enums import RunMode
from freqtrade.resolvers import StrategyResolver


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESEARCH = ROOT / "research/crypto/nfi80_extended_windows_20261126_v1"
STATE = ROOT / "user_data/deployment/nfi80-rebuy-cap-paper-20261126"
BASELINE = ROOT / "user_data/deployment/mainnet-100-20261109/futures/config.json"
CLASS = "NFIManualFlatBoostRebuyCap"
OVERLAY = {
    "strategy": CLASS,
    "strategy_path": "/opt/freqtrade/deploy/nfi80_rebuy_cap_paper/strategy",
    "max_open_trades": 14,
    "initial_state": "stopped",
}


def receipt(path):
    return {"path": str(path), "sha256": sha(path)}


def verify_reconciled(record):
    path = Path(record["path"])
    if sha(path) != record["sha256"]:
        raise ValueError("Native account receipt changed")
    summary = read(path)
    if not summary["five_minute_nav_reconciliation"]["passed"]:
        raise ValueError("A complete independently reconciled account is required")
    return summary["periods"]["continuous"]


def ranking():
    protocol_path = RESEARCH / "recent_two_year_protocol.json"
    protocol = read(protocol_path)
    sections = {}
    for cost in (protocol["primary_cost_bps_per_side"], protocol["secondary_cost_bps_per_side"]):
        controls = RESEARCH / f"recent_two_year_{cost}bp_comparison.json"
        candidate = RESEARCH / f"short_rebuy_14_wallet_{cost}bp_verified.json"
        cap = read(candidate)
        all_cases = {
            "online_archive_12": read(controls)["accounts"]["12"]["proof"],
            "fourteen": read(controls)["accounts"]["14"]["proof"],
            CLASS: cap["proof"],
        }
        results = {name: verify_reconciled(value) for name, value in all_cases.items()}
        if any(row["marks"] != protocol["window"]["five_minute_marks"] for row in results.values()):
            raise ValueError("Accounts must cover the same complete window")
        ordered = sorted(results, key=lambda name: results[name]["net_return"], reverse=True)
        sections[str(cost)] = {
            "ranking": [{"name": name, **results[name]} for name in ordered],
            "account_receipts": all_cases,
            "historical_two_cost_check_for_candidate": cap["passed"],
        }
    primary = protocol["primary_cost_bps_per_side"]
    if sections[str(primary)]["ranking"][0]["name"] != CLASS:
        raise ValueError("The existing candidate no longer leads at the frozen primary cost")
    return {"criterion": "highest_continuous_net_return_at_previously_registered_primary_cost",
            "protocol": receipt(protocol_path), "primary_bp": primary, "selected": CLASS,
            "results": sections}


def validate_native():
    original = read(BASELINE)
    config = linux_config(use_available_balance(original))
    config.update(OVERLAY)
    for field in ("api_server", "telegram", "webhook"):
        config.pop(field, None)
    if any(config["exchange"].get(key) for key in ("key", "secret", "password", "apiKey")):
        raise ValueError("Offline native validation requires the credential-free base file")
    with TemporaryDirectory(prefix="nfi-cap-live-preflight-") as directory:
        config.update(user_data_dir=Path(directory), strategy_path=str(HERE / "strategy"),
                      runmode=RunMode.LIVE)
        resolved = StrategyResolver.load_strategy(config)
        validate_config_consistency(config)
    return resolved.__class__.__name__


def main():
    chosen = ranking()
    resolved = validate_native()
    if resolved != CLASS:
        raise ValueError("Native resolver loaded an unexpected strategy")
    os.umask(0o077)
    stage = STATE / "stopped-live-candidate"
    stage.mkdir(mode=0o700, exist_ok=False)
    config = stage / "futures-overlay.json"
    config.write_text(json.dumps(OVERLAY, indent=2) + "\n")
    sources = [receipt(HERE / "strategy" / name) for name in
               (f"{CLASS}.py", "nfi_short_rebuy_capacity.py")]
    manifest = {**chosen, "offline_resolved_class": resolved, "overlay": receipt(config),
                "files": sources, "deployed": False, "orders_enabled": False}
    path = stage / "selection.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(path)


if __name__ == "__main__":
    main()
