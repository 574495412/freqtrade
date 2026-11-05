"""Freeze a forty-pair, two-incumbent historical comparison before replay."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "research/crypto/strategy_comparison_20261105"
DEST = ROOT / "research/crypto/strategy_comparison_wide_20261105"
INPUT = ROOT / "user_data/crypto_quant/matched_strategy_comparison_20261105_v002/input_review"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def record(p):
    return {"path": str(p), "sha256": sha(p)}


def save(p, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def main():
    DEST.mkdir(exist_ok=False)
    plan = json.loads((BASE / "plan.json").read_text())
    inventory_path = INPUT / "cz-comparison-data-inventory-20261105.json"
    inventory = json.loads(inventory_path.read_text())
    symbols = inventory["universes"]["forty"]
    pairs = [s.removesuffix("USDT") + "/USDT:USDT" for s in symbols]
    assert len(pairs) == len(set(pairs)) == 40
    data_dir = Path(plan["datadir"])
    original_receipts = json.loads(Path(plan["original_dataset_receipts"]["path"]).read_text())
    receipt = {}
    marks = {}
    for row in inventory["files"]:
        if row["dataset"] != "nfi40_data_v002" or row["symbol"] not in symbols:
            continue
        p = Path(row["path"])
        key = str(p.relative_to(data_dir))
        if sha(p) != row["sha256"] or original_receipts.get("candles/" + key) != row["sha256"]:
            raise ValueError("Market data receipt mismatch: " + str(p))
        if row["duplicate_timestamps"] or row["non_monotonic"] or row["null_cells"] or row["nonfinite_numeric_cells"]:
            raise ValueError("Invalid market data: " + str(p))
        if row["periods"]["full_2025_2026_08"].get("missing_rows", 0):
            raise ValueError("Market data calendar gap: " + str(p))
        receipt[key] = row["sha256"]
        if row["timeframe"] == "1h" and row["candle_type"] == "mark":
            marks[row["pair"]] = record(p)
    assert len(receipt) == 280 and len(marks) == 40
    save(DEST / "data-receipts.json", receipt)
    all_markets = json.loads((INPUT / "public-market-rules.json").read_text())
    markets = {p: all_markets["markets"][p] for p in pairs}
    tiers = {p: all_markets["leverage_tiers"][p] for p in pairs}
    assert all(markets[p]["active"] and tiers[p] for p in pairs)
    market = {**all_markets, "markets": markets, "leverage_tiers": tiers}
    save(DEST / "public-market-rules-40.json", market)
    candidates = []
    for c in plan["candidates"]:
        if c["name"] not in ("nfi-x8-1x", "nfi-x4-compact-1x"):
            continue
        config = json.loads(Path(c["config"]["path"]).read_text())
        config["exchange"]["pair_whitelist"] = pairs
        config_path = DEST / (c["name"] + ".json")
        save(config_path, config)
        candidates.append({**c, "config": record(config_path)})
    # Bound provider cache to six analyzed pairs. The storage remains lossless.
    runner_text = (ROOT / "research/crypto/run_matched_comparison.py").read_text()
    old = 'storage.DiskMapping(cache / f"analyzed_{serial}", len(plan["pairs"]))'
    new = 'storage.DiskMapping(cache / f"analyzed_{serial}", 6)'
    if runner_text.count(old) != 1:
        raise ValueError("Unexpected original runner cache implementation")
    runner_path = DEST / "run_wide_comparison.py"
    runner_path.write_text(runner_text.replace(old, new))
    plan.update({
        "schema": "cz.wide-native-strategy-comparison.v1",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "economic_results_observed_at_registration": True,
        "registration_note": "Prior six-pair and historical forty-pair X4 results were known. No outcome of this new matched forty-pair matrix was available at registration; not independent OOS.",
        "pairs": pairs, "marks": marks, "data_receipts": record(DEST / "data-receipts.json"),
        "market_snapshot": record(DEST / "public-market-rules-40.json"),
        "candidates": candidates, "total_cost_bps_per_side": [8],
        "runner_sha256": sha(runner_path), "wall_guard_seconds": 3600,
        "storage_policy": {"lossless_indicator_map_lru": 1, "lossless_trim_map_lru": 1,
                           "analyzed_provider_lru": 6,
                           "reason": "bound RAM for 40 pairs; no numerical downcast or decision change"},
        "selection_protocol": {"primary": "report both account returns, hourly marked drawdowns and complete campaigns across three registered calendar periods; no unique global winner claim",
                               "native_cash_and_order_identity_required": True,
                               "no_post_result_retuning": True},
        "known_limitations": [
            "Retrospective project-used history, not independent OOS",
            "40-pair dataset is a retrospective current-deployable intersection with possible survivorship bias; not author 223-pair universe",
            "One fixed 8bps cost scenario; no inference about 5bps or 15bps outcomes",
            "Contemporary public market rules, not certified historical exchange limits",
            "X4 retains existing float32 indicator wrapper; X8 uses full native strategy precision",
            "Equal leverage is not equal realized risk; NFI strategy logic and direction remain distinct",
            "Hourly mark OPEN is not worst intrahour drawdown or executable bid/ask",
            "Only two candidates, not a global optimum or live qualification"
        ],
        "input_inventory": record(inventory_path),
        "original_full_market_snapshot": record(INPUT / "public-market-rules.json"),
        "comparison_purpose": "Same forty-pair wallet and historical window for unchanged NFI X8 and existing X4 compact; no financial account or result at registration",
        "new_factors": 0, "parameter_optimization": False,
        "orders_enabled": False, "production_eligible": False,
    })
    save(DEST / "plan.json", plan)
    print(json.dumps({"plan": str(DEST / "plan.json"), "sha256": sha(DEST / "plan.json"),
                      "pairs": len(pairs), "files": len(receipt),
                      "candidates": [c["name"] for c in candidates]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
