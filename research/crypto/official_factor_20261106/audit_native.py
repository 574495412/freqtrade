"""Read-only audit of frozen inputs and native exports; never reruns a strategy.

Amounts are USDT. Price P&L is an accounting component on the observed fills,
not the return of a hypothetical zero-fee strategy (which would trade differently).
"""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def grouped(trades, key):
    groups = defaultdict(lambda: {"trades": 0, "profit_abs": 0.0})
    for t in trades:
        g = groups[key(t)]
        g["trades"] += 1
        g["profit_abs"] += t["profit_abs"]
    return [{"key": k, **v} for k, v in sorted(groups.items())]


def concentration(groups):
    negative = sorted((g for g in groups if g["profit_abs"] < 0), key=lambda g: g["profit_abs"])
    denominator = -sum(g["profit_abs"] for g in negative)
    return {"denominator": "sum of absolute negative group net P&L, not account net loss",
            "negative_groups": len(negative), "negative_group_loss_usdt": denominator,
            "worst_three": negative[:3],
            "worst_three_loss_share": -sum(g["profit_abs"] for g in negative[:3]) / denominator if denominator else None}


def accounts(results):
    rows = []
    for row in results["rows"]:
        path = ROOT / row["archive"]
        assert sha(path) == row["archive_sha256"], path
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith(".json") and not n.endswith("_config.json"))
            s = json.loads(z.read(name))["strategy"][row["strategy"]]
        trades = s["trades"]
        gross = fees = funding = max_error = 0.0
        for t in trades:
            assert not t["is_short"] and t["leverage"] == 1 and not t["is_open"]
            assert len(t["orders"]) == 2
            entry, close = t["orders"]
            assert entry["ft_is_entry"] and not close["ft_is_entry"]
            assert abs(entry["amount"] - t["amount"]) < 1e-8
            assert abs(close["amount"] - t["amount"]) < 1e-8
            assert entry["safe_price"] == t["open_rate"] and close["safe_price"] == t["close_rate"]
            g = t["amount"] * (t["close_rate"] - t["open_rate"])
            f = t["amount"] * (t["open_rate"] * t["fee_open"] + t["close_rate"] * t["fee_close"])
            u = t["funding_fees"]
            assert u is not None
            error = abs(g - f + u - t["profit_abs"])
            assert error < 1e-6, (row["id"], error)
            gross += g
            fees += f
            funding += u
            max_error = max(max_error, error)
        net = sum(t["profit_abs"] for t in trades)
        assert abs(net - s["profit_total_abs"]) < 1e-6
        assert abs(gross - fees + funding - net) < 1e-4
        pairs = grouped(trades, lambda t: t["pair"])
        days = grouped(trades, lambda t: t["close_date"][:10])
        exits = grouped(trades, lambda t: t["exit_reason"])
        for official in s["results_per_pair"]:
            if official["key"] == "TOTAL":
                continue
            observed = next((g["profit_abs"] for g in pairs if g["key"] == official["key"]), 0)
            assert abs(observed - official["profit_total_abs"]) < 1e-6
        rows.append({"id": row["id"], "strategy": row["strategy"], "market": row["market"],
                     "window": row["window"], "cost": row["cost"], "archive_sha256": sha(path),
                     "trades": len(trades), "price_pnl_observed_fills": gross,
                     "commission_and_execution_proxy": fees, "funding_signed_income": funding,
                     "net_pnl": net, "max_trade_identity_error": max_error,
                     "pair_concentration": concentration(pairs), "day_concentration": concentration(days),
                     "exit_reasons": exits, "native_months": s["periodic_breakdown"]["month"],
                     "rejected_signals": s["rejected_signals"],
                     "pairs_with_trades": len(pairs), "native_pairlist_count": len(s["pairlist"])})
    return rows


def data_quality(plan):
    records = []
    for market in ("spot", "futures"):
        for window in ("L", "S"):
            run = next(r for r in plan["runs"] if r["market"] == market and r["window"] == window)
            receipt = ROOT / run["data_receipt"]["path"]
            assert sha(receipt) == run["data_receipt"]["sha256"]
            hashes = json.loads(receipt.read_text())
            start, end = [pd.Timestamp(d, tz="UTC") for d in plan["windows"][window]]
            kinds = [(tf, market) for tf in ("5m", "1h", "4h")]
            if market == "futures":
                kinds += [("1h", "mark"), ("1h", "funding_rate")]
            for pair in plan["markets"][market]["pairs"]:
                for tf, kind in kinds:
                    filename = pair.replace("/", "_").replace(":", "_") + "-" + tf
                    relative = filename + ".feather" if market == "spot" else "futures/" + filename + "-" + kind + ".feather"
                    path = ROOT / run["datadir"] / relative
                    digest = sha(path)
                    assert digest == hashes[relative], path
                    raw = pd.read_feather(path)
                    d = raw.loc[(raw.date >= start) & (raw.date < end)].copy()
                    assert len(d), path
                    dates = pd.DatetimeIndex(d.date)
                    delta = pd.Timedelta(minutes={"5m": 5, "1h": 60, "4h": 240}[tf])
                    rec = {"market": market, "window": window, "pair": pair, "timeframe": tf, "kind": kind,
                           "path": str(path.relative_to(ROOT)), "sha256": digest, "rows": len(d),
                           "first": str(dates.min()), "last": str(dates.max()),
                           "duplicates": int(dates.duplicated().sum()), "sorted": bool(dates.is_monotonic_increasing),
                           "null_cells": int(d.isna().sum().sum())}
                    numeric = d.drop(columns="date").to_numpy(dtype=float)
                    rec["nonfinite_numeric_cells"] = int((~np.isfinite(numeric)).sum())
                    if kind == "funding_rate":
                        gaps = d.date.diff().dt.total_seconds().dropna()
                        rec["event_interval_hours_histogram"] = {str(k/3600): int(v) for k, v in gaps.value_counts().sort_index().items()}
                        rec["event_schedule_complete"] = "UNVERIFIED: filename 1h is not an event schedule; historical interval rules required"
                        rec["zero_rate_events"] = int((d.funding_rate == 0).sum())
                    else:
                        expected = pd.date_range(start, end, freq=delta, inclusive="left")
                        missing = expected.difference(dates)
                        rec.update(expected_rows=len(expected), missing_timestamps=len(missing),
                                   missing_examples=[str(x) for x in missing[:10]],
                                   off_grid_timestamps=len(dates.difference(expected)),
                                   nonpositive_price_rows=int((d[["open", "high", "low", "close"]] <= 0).any(axis=1).sum()),
                                   invalid_ohlc_rows=int(((d.high < d[["open", "close", "low"]].max(axis=1)) | (d.low > d[["open", "close", "high"]].min(axis=1))).sum()),
                                   negative_volume_rows=int((d.volume < 0).sum()), zero_volume_rows=int((d.volume == 0).sum()))
                        if tf == "1h" and kind == market:
                            warm = raw.loc[(raw.date >= start - 20*delta) & (raw.date < start)]
                            rec["adx_startup_missing_timestamps"] = len(pd.date_range(start - 20*delta, start, freq=delta, inclusive="left").difference(pd.DatetimeIndex(warm.date)))
                    records.append(rec)
    return records


def main():
    results_path = HERE / "results.json"
    results = json.loads(results_path.read_text())
    plan = json.loads((HERE / "plan.json").read_text())
    rows = accounts(results)
    files = data_quality(plan)
    output = {"created_at_utc": datetime.now(timezone.utc).isoformat(),
              "results_sha256": sha(results_path), "analysis_source_sha256": sha(Path(__file__)),
              "financial_runs_added": 0, "causality_diagnostics_added": 0,
              "scope": "Accounting decomposition of native trades and frozen-file timestamp/shape checks. No raw-exchange receipt or funding schedule completeness certification.",
              "accounts": rows, "data_files": files}
    destination = HERE / "native_audit.json"
    with destination.open("x") as f:
        json.dump(output, f, indent=2, allow_nan=False)
        f.write("\n")
    print(json.dumps({"accounts": len(rows), "trades": sum(r["trades"] for r in rows), "files": len(files),
                      "files_with_missing_ohlcv": sum(r.get("missing_timestamps", 0) > 0 for r in files),
                      "duplicates": sum(r["duplicates"] for r in files)}))


if __name__ == "__main__":
    main()
