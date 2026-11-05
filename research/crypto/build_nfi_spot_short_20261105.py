"""Build the frozen 36-pair spot short-window dataset and account plan."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from freqtrade.research.data import read_archive  # noqa: E402

STUDY = ROOT / "research/crypto/spot_baseline_20261105"
RAW = ROOT / "user_data/crypto_quant/spot_baseline_inputs_20261105_v001/short-raw"
OUT = ROOT / "user_data/crypto_quant/spot_baseline_data_short_20261105_v001"
CZ = Path("/Users/chrise/GitHub/CZ-Agent")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record(path):
    return {"path": str(path), "sha256": sha(path)}


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    protocol = json.loads((STUDY / "protocol.json").read_text())
    inventory = json.loads((STUDY / "spot-data-inventory.json").read_text())
    receipt = json.loads((RAW / "download-receipt.json").read_text())
    if sha(STUDY / "protocol.json") != receipt["protocol_sha256"] or sha(RAW / "sources.json") != receipt["sources_sha256"]:
        raise ValueError("Spot protocol or source manifest changed")
    if any(r["missing_rows"] or r["outside_rows"] or r["nonfinite_cells"] or
           (r["duplicates"] and (r["symbol"], r["timeframe"], r["duplicates"]) != ("AXSUSDT", "1d", 1))
           for r in inventory["records"]):
        raise ValueError("Spot data incomplete; investigate before any account replay")
    sources = json.loads((RAW / "sources.json").read_text())
    grouped = {}
    for source in sources:
        if sha(Path(source["path"])) != source["sha256"]:
            raise ValueError("Spot source archive changed")
        grouped.setdefault((source["symbol"], source["timeframe"]), []).append(source)
    OUT.mkdir(parents=True, exist_ok=False)
    data = OUT / "candles"
    data.mkdir()
    receipt_files, marks, coverage = {}, {}, {}
    fields = ["date", "open", "high", "low", "close", "volume"]
    for index, pair in enumerate(protocol["spot_pairs_registered"], 1):
        symbol = pair.replace("/", "")
        raw = {}
        for timeframe in ("5m", "4h", "1d"):
            frame = pd.concat([read_archive(Path(row["path"])) for row in grouped[(symbol, timeframe)]], ignore_index=True)
            if symbol == "AXSUSDT" and timeframe == "1d":
                duplicate = frame.loc[frame.date.duplicated(keep=False)].reset_index(drop=True)
                api_path = RAW / "axs-duplicate-api-20260210.json"
                api = json.loads(api_path.read_text())["body"]
                if (len(duplicate) != 2 or not duplicate.iloc[0].equals(duplicate.iloc[1])
                    or str(duplicate.iloc[0].date) != "2026-02-10 00:00:00+00:00" or len(api) != 1):
                    raise ValueError("AXS source duplicate is not exactly identical")
                reference = [float(api[0][i]) for i in range(1, 6)]
                if reference != [float(duplicate.iloc[0][name]) for name in fields[1:]]:
                    raise ValueError("AXS duplicate does not match official spot API")
                frame = frame.drop_duplicates(subset=["date"], keep="first")
            raw[timeframe] = frame.sort_values("date").reset_index(drop=True)[fields]
        for timeframe, frequency in (("15m", "15min"), ("1h", "1h")):
            raw[timeframe] = (raw["5m"].set_index("date").resample(frequency, origin="epoch")
                              .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
                              .reset_index())
        for timeframe, frame in raw.items():
            path = data / f"{pair.replace('/', '_')}-{timeframe}.feather"
            frame.to_feather(path, compression="lz4", compression_level=9)
            receipt_files[path.name] = sha(path)
            if timeframe == "1h":
                marks[pair] = record(path)
        coverage[symbol] = {tf: len(frame) for tf, frame in raw.items()}
        if index % 6 == 0:
            print(f"Spot native inputs built {index}/{len(protocol['spot_pairs_registered'])}", flush=True)
    if len(receipt_files) != 180 or len(marks) != 36:
        raise ValueError("Unexpected spot dataset size")
    save(OUT / "data-receipts.json", receipt_files)
    save(OUT / "coverage.json", coverage)
    save(OUT / "duplicate-resolution.json", {"symbol": "AXSUSDT", "timeframe": "1d", "date": "2026-02-10",
                                             "action": "drop one exactly identical archive row after public API corroboration",
                                             "raw_api": record(RAW / "axs-duplicate-api-20260210.json"),
                                             "original_inventory": record(STUDY / "spot-data-inventory.json")})
    common = json.loads((ROOT / "research/crypto/strategy_comparison_wide_20261105/plan.json").read_text())
    old_candidates = json.loads((ROOT / "research/crypto/strategy_comparison_20261105/plan.json").read_text())["candidates"]
    base_config = json.loads((CZ / "ops/freqtrade/nfi-x8-binance-spot.json").read_text())
    candidates = []
    for old_name in ("nfi-x8-1x", "nfi-x7-1x"):
        candidate = next(c for c in old_candidates if c["name"] == old_name)
        name = old_name.replace("-1x", "-spot")
        config = {**base_config, "strategy": candidate["strategy"]}
        cp = STUDY / (name + ".json")
        with cp.open("x") as stream:
            json.dump(config, stream, indent=2)
            stream.write("\n")
        candidates.append({**candidate, "name": name, "config": record(cp),
                           "source_and_risk_behavior": "unchanged upstream native spot signals and position lifecycle; long-only"})
    common.update({"schema": "cz.matched-spot-accounts.v1", "registered_at": datetime.now(timezone.utc).isoformat(),
                   "economic_results_observed_at_registration": False,
                   "registration_note": "Spot protocol and candidates were registered before these spot economic results. Futures and author reports were already known; this short historical window is not prospective paper.",
                   "spot_protocol": record(STUDY / "protocol.json"), "spot_data_inventory": record(STUDY / "spot-data-inventory.json"),
                   "spot_duplicate_resolution": record(OUT / "duplicate-resolution.json"),
                   "start": protocol["prioritized_period"]["start"], "end": protocol["prioritized_period"]["end"],
                   "comparison_end": "2026-11-04T23:00:00Z", "pairs": protocol["spot_pairs_registered"],
                   "initial_equity_usdt": 1000, "allowed_leverage": 1.0,
                   "total_cost_bps_per_side": protocol["fees_bps_per_side"], "cost_model": protocol["fee_model"],
                   "datadir": str(data), "data_receipts": record(OUT / "data-receipts.json"), "marks": marks,
                   "market_snapshot": record(STUDY / "ccxt-spot-market-rules.json"),
                   "observer_source": record(STUDY / "spot_observer.py"), "runner_sha256": sha(STUDY / "run_spot_comparison.py"),
                   "candidates": candidates,
                   "periods": [{"name": "post-source-period", "start": protocol["prioritized_period"]["start"],
                                "end": "2026-11-04T23:00:00Z"}],
                   "known_limitations": ["Retrospective short post-source period; not prospective forward evidence",
                                         "Independent 1000 USDT spot wallets per strategy, not shared capital or futures equity",
                                         "36-pair author/current-market intersection has survivorship limitations",
                                         "Hourly spot OPEN is not a futures mark, executable bid/ask or intrahour worst drawdown",
                                         "Current public exchange filters are not verified point-in-time historical rules",
                                         "Short sample cannot establish a long-run winner or production eligibility"],
                   "selection_protocol": {"primary": "report two spot strategies under two cost proxies with full hourly spot reference NAV and independent cash audit; no global optimum claim",
                                          "no_post_result_retuning": True},
                   "orders_enabled": False, "production_eligible": False})
    common.pop("original_dataset_receipts", None)
    save(STUDY / "plan-short.json", common)
    save(OUT / "completion.json", {"files": len(receipt_files), "plan_sha256": sha(STUDY / "plan-short.json"),
                                    "orders_enabled": False})
    print(json.dumps({"files": len(receipt_files), "plan": str(STUDY / 'plan-short.json'),
                      "sha256": sha(STUDY / 'plan-short.json')}))


if __name__ == "__main__":
    main()
