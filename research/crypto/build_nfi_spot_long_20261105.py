"""Build contiguous exchange-sourced spot history for frozen long comparison."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from freqtrade.research.data import read_archive

STUDY = ROOT / "research/crypto/spot_baseline_20261105"
REV = ROOT / "research/crypto/spot_baseline_20261105_v003"
RAW = ROOT / "user_data/crypto_quant/spot_baseline_inputs_20261105_v001/long-raw"
SHORT = ROOT / "user_data/crypto_quant/spot_baseline_data_short_20261105_v001"
OUT = ROOT / "user_data/crypto_quant/spot_baseline_data_long_20261105_v001"

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def record(path):
    return {"path": str(path), "sha256": sha(path)}

def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")

def main():
    protocol = json.loads((STUDY / "protocol.json").read_text())
    inv = json.loads((STUDY / "spot-long-data-inventory.json").read_text())
    receipt = json.loads((RAW / "download-receipt.json").read_text())
    if sha(STUDY / "protocol.json") != receipt["protocol_sha256"] or sha(RAW / "sources.json") != receipt["sources_sha256"] or receipt["failed"]:
        raise ValueError("Archive manifest or protocol mismatch")
    if any(r["missing_rows"] or r["outside_rows"] or r["nonfinite_cells"] or
           (r["duplicates"] and (r["symbol"], r["timeframe"], r["duplicates"]) != ("AVAXUSDT", "4h", 1)) for r in inv["records"]):
        raise ValueError("Investigate incomplete long dataset")
    sources = json.loads((RAW / "sources.json").read_text())
    grouped = {}
    for src in sources:
        if sha(Path(src["path"])) != src["sha256"]:
            raise ValueError("Source archive changed")
        grouped.setdefault((src["symbol"], src["timeframe"]), []).append(src)
    short_receipts = json.loads((SHORT / "data-receipts.json").read_text())
    OUT.mkdir(parents=True, exist_ok=False)
    data = OUT / "candles"
    data.mkdir()
    files, marks, coverage = {}, {}, {}
    fields = ["date", "open", "high", "low", "close", "volume"]
    for index, pair in enumerate(protocol["spot_pairs_registered"], 1):
        symbol = pair.replace("/", "")
        native = {}
        for tf, freq in (("5m", "5min"), ("4h", "4h"), ("1d", "1d")):
            history = pd.concat([read_archive(Path(s["path"])) for s in grouped[(symbol, tf)]], ignore_index=True)
            if symbol == "AVAXUSDT" and tf == "4h":
                dup = history.loc[history.date.duplicated(keep=False)].reset_index(drop=True)
                api = json.loads((RAW / "avax-duplicate-api-20260506.json").read_text())["body"]
                if len(dup) != 2 or not dup.iloc[0].equals(dup.iloc[1]) or str(dup.iloc[0].date) != "2026-05-06 00:00:00+00:00" or len(api) != 1 or [float(api[0][i]) for i in range(1, 6)] != [float(dup.iloc[0][c]) for c in fields[1:]]:
                    raise ValueError("AVAX duplicate not corroborated")
                history = history.drop_duplicates(subset=["date"], keep="first")
            short_path = SHORT / "candles" / f"{pair.replace('/', '_')}-{tf}.feather"
            if sha(short_path) != short_receipts[short_path.name]:
                raise ValueError("Short dataset changed")
            frame = pd.concat([history[fields], pd.read_feather(short_path)[fields]], ignore_index=True).sort_values("date").reset_index(drop=True)
            if frame.date.duplicated().any() or not frame.date.diff().iloc[1:].eq(pd.Timedelta(freq)).all():
                raise ValueError(f"Gap or duplicate across long/short seam: {pair} {tf}")
            native[tf] = frame
        for tf, freq in (("15m", "15min"), ("1h", "1h")):
            native[tf] = native["5m"].set_index("date").resample(freq, origin="epoch").agg({"open":"first", "high":"max", "low":"min", "close":"last", "volume":"sum"}).reset_index()
        for tf, frame in native.items():
            path = data / f"{pair.replace('/', '_')}-{tf}.feather"
            frame.to_feather(path, compression="lz4", compression_level=9)
            files[path.name] = sha(path)
            if tf == "1h":
                marks[pair] = record(path)
        coverage[symbol] = {tf: len(f) for tf, f in native.items()}
        if index % 6 == 0:
            print(f"Long spot inputs built {index}/{len(protocol['spot_pairs_registered'])}", flush=True)
    if len(files) != 180 or len(marks) != 36:
        raise ValueError("Unexpected dataset size")
    save(OUT / "data-receipts.json", files)
    save(OUT / "coverage.json", coverage)
    save(OUT / "duplicate-resolution.json", {"symbol":"AVAXUSDT", "timeframe":"4h", "date":"2026-05-06T00:00:00Z", "action":"drop one identical row after official spot API corroboration", "raw_api":record(RAW / "avax-duplicate-api-20260506.json"), "inventory":record(STUDY / "spot-long-data-inventory.json"), "short_duplicate_resolution":record(SHORT / "duplicate-resolution.json")})
    plan = json.loads((REV / "plan-short.json").read_text())
    base = json.loads((ROOT / "research/crypto/strategy_comparison_wide_20261105/plan.json").read_text())
    plan.update(registered_at=datetime.now(timezone.utc).isoformat(), economic_results_observed_at_registration=True,
                registration_note="Candidates, long-window intent, costs and eligibility frozen in spot protocol before spot results; short-window account results were available before this detailed long plan was registered. Long results not yet observed.",
                start=base["start"], end=base["end"], comparison_end=base["comparison_end"], periods=base["periods"], datadir=str(data), data_receipts=record(OUT / "data-receipts.json"), marks=marks,
                spot_long_data_inventory=record(STUDY / "spot-long-data-inventory.json"), spot_long_duplicate_resolution=record(OUT / "duplicate-resolution.json"))
    plan["known_limitations"] = [x for x in plan["known_limitations"] if not x.startswith("Retrospective short") and not x.startswith("Short sample")]
    plan["known_limitations"].extend(["Retrospective long window predates October 2026 strategy source publication", "Short-period results known before detailed long plan registration; long protocol frozen earlier"])
    save(REV / "plan-long.json", plan)
    save(OUT / "completion.json", {"files": len(files), "plan_sha256": sha(REV / "plan-long.json"), "orders_enabled": False})
    print(json.dumps({"files":len(files), "plan":str(REV / "plan-long.json"), "sha256":sha(REV / "plan-long.json")}))

if __name__ == "__main__":
    main()
