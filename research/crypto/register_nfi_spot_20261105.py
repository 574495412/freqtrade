"""Register a separate native Binance spot study before spot economic results."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import rapidjson
import requests

ROOT = Path(__file__).resolve().parents[2]
CZ = Path("/Users/chrise/GitHub/CZ-Agent")
AUTHOR = ROOT / "user_data/crypto_quant/nfi_original_20261027_v001/fork_workspace"
OUT = ROOT / "research/crypto/spot_baseline_20261105"
RAW = ROOT / "user_data/crypto_quant/spot_baseline_inputs_20261105_v001"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def main():
    OUT.mkdir(exist_ok=False)
    RAW.mkdir(parents=True, exist_ok=False)
    source_list = AUTHOR / "configs/pairlist-backtest-static-binance-spot-usdt.json"
    expected_sha = "0f81d7ce509094f453fad3dec6ed40c9bae15d4a8326244521676c2a1be713a5"
    if sha(source_list) != expected_sha:
        raise ValueError("Original pinned author spot whitelist changed")
    author = rapidjson.loads(source_list.read_text(), parse_mode=rapidjson.PM_COMMENTS | rapidjson.PM_TRAILING_COMMAS)
    author_pairs = set(author["exchange"]["pair_whitelist"])
    wide = json.loads((ROOT / "research/crypto/strategy_comparison_wide_20261105/plan.json").read_text())
    response = requests.get("https://api.binance.com/api/v3/exchangeInfo", timeout=30)
    response.raise_for_status()
    raw_path = RAW / "binance-spot-exchange-info.json"
    raw_path.write_bytes(response.content)
    exchange = response.json()
    markets = {m["symbol"]: m for m in exchange["symbols"]
               if m["status"] == "TRADING" and m.get("isSpotTradingAllowed")}
    names = [p.split("/")[0] for p in wide["pairs"]]
    pairs = [base + "/USDT" for base in names if base + "/USDT" in author_pairs and base + "USDT" in markets]
    if len(pairs) != 36 or "BTC/USDT" not in pairs:
        raise ValueError("Spot universe differs from inspected source; inspect current venue changes")
    current = {pair: markets[pair.replace("/", "")] for pair in pairs}
    spot_info = {"observed_at_utc": datetime.now(timezone.utc).isoformat(),
                 "url": "https://api.binance.com/api/v3/exchangeInfo", "raw_sha256": sha(raw_path),
                 "markets": current, "historical_rules_verified": False, "credentials_used": False}
    save(OUT / "public-spot-market-rules.json", spot_info)
    six_plan = json.loads((ROOT / "research/crypto/strategy_comparison_20261105/plan.json").read_text())
    x8 = next(c for c in six_plan["candidates"] if c["name"] == "nfi-x8-1x")
    x7 = next(c for c in six_plan["candidates"] if c["name"] == "nfi-x7-1x")
    plan = {
        "schema": "cz.native-spot-research-protocol.v1", "registered_at_utc": datetime.now(timezone.utc).isoformat(),
        "spot_economic_results_observed": False,
        "selection_timing": "Spot protocol registered after futures and author report outcomes were known; no new spot account outcomes observed.",
        "engine_commit": "c051e9065c5b677f03817dd1f83cb980af1ed684", "author_commit": "1af55971d3a64703ec449078e09fdec257d0d3c6",
        "author_spot_pairlist": {"path": str(source_list), "sha256": expected_sha},
        "venue_snapshot": {"path": str(OUT / 'public-spot-market-rules.json'), "sha256": sha(OUT / 'public-spot-market-rules.json')},
        "universe_rule": "intersection of 40-pair futures research set, exact pinned author spot backtest whitelist, and current Binance trading-enabled spot USDT markets; ordering follows 40-pair set",
        "spot_pairs_registered": pairs, "excluded_from_prior_40": [b + '/USDT' for b in names if b + '/USDT' not in pairs],
        "data_eligibility_rule": "Before economic replay, exclude a pair only if public spot archives and official API cannot establish complete required warmup or all evaluation bars; preserve failure and reason. Never exclude based on economic results. BTC/USDT is mandatory. Record survivorship bias.",
        "candidates": [{"name": c["name"], "strategy": c["strategy"], "source_files": c["source_files"]} for c in (x8, x7)],
        "market": "binance spot", "settlement": "USDT", "long_only": True,
        "initial_equity_usdt_per_independent_wallet": 1000, "max_open_trades": 6,
        "fees_bps_per_side": [13, 20],
        "fee_model": "10bps public regular-user spot fee plus 3/10bps execution-cost proxy per side; not proof of actual account tier, discount or slippage",
        "fee_source": "https://www.binance.com/en/fee/trading", "funding": "none for spot holdings",
        "prioritized_period": {"start": "2026-10-23T00:00:00Z", "end": "2026-11-05T00:00:00Z", "meaning": "retrospective short window after pinned source commit, starts from cash"},
        "long_history_target": {"start": "2025-01-01T00:00:00Z", "end": "2026-09-01T00:00:00Z", "meaning": "previously used historical period, not independent OOS"},
        "valuation": "hourly spot candle OPEN with open inventory, actual simulated fills and fees; not futures mark or executable bid/ask",
        "constraints": ["two separate strategy wallets; not shared-account combination", "spot and futures capital are not implicitly shared or double counted", "author/current intersection may introduce survivorship", "published spot summary is not a reproduced continuous account"],
        "orders_enabled": False, "production_eligible": False,
    }
    save(OUT / "protocol.json", plan)
    print(json.dumps({"protocol": str(OUT / 'protocol.json'), "sha256": sha(OUT / 'protocol.json'),
                      "spot_pairs": len(pairs), "spot_economic_results_observed": False}))


if __name__ == "__main__":
    main()
