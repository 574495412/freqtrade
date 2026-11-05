"""Compare frozen native funding to prior raw events and irregular API windows."""
import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "user_data/crypto_quant/nfi40_data_v002"
OUT = ROOT / "research/crypto/strategy_comparison_wide_20261105/funding-audit.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    receipts = json.loads((DATA / "receipts.json").read_text())
    symbols = json.loads((DATA / "plan.json").read_text())["symbols"]
    irregular = []
    count = 0
    for symbol in symbols:
        source = DATA / (symbol + "_funding_events.parquet")
        native = DATA / "candles/futures" / (symbol.removesuffix("USDT") + "_USDT_USDT-1h-funding_rate.feather")
        frame = pd.read_parquet(source)
        rendered = pd.read_feather(native)
        if sha(source) != receipts[source.name] or not frame[["date", "funding_rate"]].equals(rendered):
            raise ValueError("Funding source or native values differ: " + symbol)
        count += len(frame)
        deltas = frame.date.diff().dt.total_seconds() / 3600
        for ix in frame.index[(deltas != frame.funding_interval_hours) & deltas.notna()]:
            start = int(frame.loc[ix - 1, "date"].timestamp() * 1000)
            end = int(frame.loc[ix, "date"].timestamp() * 1000)
            raw = DATA / "funding_api" / f"{symbol}_{start}_{end + 1000}.json"
            evidence = json.loads(raw.read_text())
            body = evidence["body"]
            middle = [r for r in body if start + 1000 < r["fundingTime"] < end]
            if middle:
                raise ValueError("API recorded an extra funding event: " + str(raw))
            irregular.append({"symbol": symbol, "from": str(frame.loc[ix - 1, "date"]),
                              "to": str(frame.loc[ix, "date"]),
                              "elapsed_hours": float(deltas[ix]),
                              "previous_declared_hours": int(frame.loc[ix - 1, "funding_interval_hours"]),
                              "current_declared_hours": int(frame.loc[ix, "funding_interval_hours"]),
                              "raw_api_response": str(raw), "raw_api_response_sha256": sha(raw),
                              "api_events_between_boundaries": len(middle)})
    result = {"symbols": len(symbols), "events": count, "native_values_equal_source": True,
              "interval_discrepancies": irregular,
              "qualification": "Existing public funding-rate API responses cover each discrepancy and show no additional intermediate event. An interval change or absent settlement is not filled synthetically; historical account settlement remains unverified.",
              "orders_enabled": False}
    with OUT.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"symbols": len(symbols), "events": count,
                      "irregular_intervals": len(irregular), "path": str(OUT), "sha256": sha(OUT)}))


if __name__ == "__main__":
    main()
