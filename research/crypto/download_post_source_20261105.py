"""Download fixed post-source historical inputs; no economic evaluation."""
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from freqtrade.research.data import download_mark  # noqa: E402
from research.crypto.prepare_nfi import archive  # noqa: E402
from research.crypto.prepare_nfi_universe import funding_archive  # noqa: E402

PROTOCOL = ROOT / "research/crypto/strategy_comparison_wide_20261105/post-source-period-protocol.json"
OUT = ROOT / "user_data/crypto_quant/nfi40_post_source_inputs_20261105_v001"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    tmp = path.with_suffix(path.suffix + ".writing")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def funding_api(symbol):
    url = "https://fapi.binance.com/fapi/v1/fundingRate"
    start = pd.Timestamp("2026-11-01T00:00:00Z")
    end = pd.Timestamp("2026-11-05T00:00:00Z")
    params = {"symbol": symbol, "startTime": int(start.timestamp() * 1000),
              "endTime": int(end.timestamp() * 1000) - 1, "limit": 1000}
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, list) or not body or len(body) >= 1000:
        raise ValueError("Missing or truncated public funding history: " + symbol)
    if any(r["symbol"] != symbol or not params["startTime"] <= r["fundingTime"] <= params["endTime"] for r in body):
        raise ValueError("Unexpected public funding symbol or clock: " + symbol)
    path = OUT / "funding_api" / (symbol + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    save(path, {"url": url, "params": params, "body": body,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "orders_enabled": False})
    return {"kind": "funding_api", "symbol": symbol, "path": str(path), "sha256": sha(path)}


def execute(job):
    kind, symbol, period = job
    if kind in ("5m", "1d"):
        return {"kind": "futures", **archive(symbol, kind, period, OUT / "raw")}
    if kind == "mark":
        return {"kind": "mark", **download_mark((symbol, period, OUT / "mark_raw"))}
    if kind == "funding":
        return funding_archive(symbol, period, OUT / "funding_raw")
    return funding_api(symbol)


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    (OUT / "registered_protocol.json").write_bytes(PROTOCOL.read_bytes())
    plan = json.loads(PROTOCOL.read_text())
    symbols = [p.split("/")[0] + "USDT" for p in plan["pairs"]]
    periods = ["2026-09", "2026-10", *[f"2026-11-{day:02}" for day in range(1, 5)]]
    jobs = [(kind, sym, period) for sym in symbols for kind in ("5m", "1d", "mark") for period in periods]
    jobs += [("funding", sym, month) for sym in symbols for month in ("2026-09", "2026-10")]
    jobs += [("funding_api", sym, None) for sym in symbols]
    sources, failures = [], []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(execute, job): job for job in jobs}
        for future in as_completed(futures):
            try:
                sources.append(future.result())
            except Exception as error:
                failures.append({"job": futures[future], "error": str(error)})
            done = len(sources) + len(failures)
            if done % 40 == 0 or done == len(jobs):
                save(OUT / "progress.json", {"completed": done, "total": len(jobs),
                                             "failed": len(failures)})
                print(f"Downloaded {done}/{len(jobs)}, failures={len(failures)}", flush=True)
    save(OUT / "sources.json", sorted(sources, key=lambda x: (x["symbol"], x["kind"], x["path"])))
    save(OUT / "failures.json", failures)
    if failures:
        raise SystemExit("Download incomplete; no account may use this dataset")
    save(OUT / "download-completion.json", {"jobs": len(jobs), "sources_sha256": sha(OUT / "sources.json"),
                                            "registered_protocol_sha256": sha(OUT / "registered_protocol.json"),
                                            "orders_enabled": False})


if __name__ == "__main__":
    main()
