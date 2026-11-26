"""Native empirical precision versus strictly completed past bars, no new wallet.

This diagnoses information use in the original price-precision heuristic. A
past-price precision proxy is not a historical Binance trading-rule snapshot.
Regridding a recorded fill is not a replay of hypothetical orders or profits.
"""
import gc
import hashlib
import json
import re
import sys
from decimal import Decimal as D
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]
ENGINE = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")
sys.path.insert(0, str(ENGINE))

from freqtrade.configuration import TimeRange
from freqtrade.data.btanalysis.historic_precision import get_tick_size_over_time
from freqtrade.data.history import load_data
from freqtrade.enums import CandleType

from account_audit import stats_from


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def strict_past_count(frame):
    # The native function has computed the observed per-bar digit count. At
    # a five-minute opening boundary only earlier completed bars are known.
    months = frame.date.dt.strftime("%Y-%m")
    known = frame.max_count.groupby(months).cummax().groupby(months).ffill().shift(1).ffill()
    return known


def main():
    plan_path = ROOT / "research/crypto/strategy_comparison_wide_20261105/plan.json"
    plan = json.loads(plan_path.read_text())
    manifest = json.loads(Path(plan["engine_manifest"]["path"]).read_text())
    for rel, digest in manifest["files"].items():
        assert sha(ENGINE / rel) == digest
    receipts = json.loads(Path(plan["data_receipts"]["path"]).read_text())
    for rel, digest in receipts.items():
        assert sha(Path(plan["datadir"]) / rel) == digest
    markets = json.loads(Path(plan["market_snapshot"]["path"]).read_text())["markets"]
    orders_by_pair = {pair: [] for pair in plan["pairs"]}
    startup_counts, log_hashes = set(), {}
    for root in ["wide_strategy_comparison_20261105_v001", "wide_strategy_cost15_20261105_v001",
                 "wide_strategy_x7_20261105_v001"]:
        for folder in (ROOT / "user_data/crypto_quant" / root).iterdir():
            if not folder.is_dir() or not (folder / "result.json").exists():
                continue
            log = folder.parent / (folder.name + ".log")
            values = re.findall(r"Strategy using startup_candle_count: (\d+)", log.read_text())
            assert len(values) == 1
            startup_counts.add(int(values[0]))
            log_hashes[str(log)] = sha(log)
            archive = next(folder.glob("native_results/*.zip"))
            native = stats_from(archive)
            for number, trade in enumerate(native["trades"]):
                for index, order in enumerate(trade["orders"]):
                    orders_by_pair[trade["pair"]].append({"account": folder.name,
                        "trade_index": number, "archive_order_index": index,
                        "at": pd.Timestamp(order["order_filled_timestamp"], unit="ms", tz="UTC"),
                        "filled_price": order["safe_price"]})
    assert len(startup_counts) == 1
    startup = next(iter(startup_counts))
    start = int(pd.Timestamp(plan["start"]).timestamp())
    stop = int(pd.Timestamp(plan["end"]).timestamp()) - 1
    timerange = TimeRange.parse_timerange(f"{start}-{stop}")
    summaries, findings, bars, mismatches, inspected, fills_differ = [], [], 0, 0, 0, 0
    for pair in plan["pairs"]:
        # Exact resolved startup count comes from each original native process log.
        frame = load_data(datadir=Path(plan["datadir"]), pairs=[pair], timeframe="5m",
            timerange=timerange, startup_candles=startup, fail_without_data=True,
            data_format="feather", candle_type=CandleType.FUTURES)[pair]
        assert frame.date.is_unique and frame.date.is_monotonic_increasing
        native_monthly = get_tick_size_over_time(frame)
        assert {"max_count", "open_count", "high_count", "low_count", "close_count"} <= set(frame)
        prefix_count = strict_past_count(frame)
        past_tick = 1 / 10 ** prefix_count
        fallback = markets[pair]["precision"]["price"]
        past_tick = past_tick.fillna(fallback)
        # Actual native.asof skips NaN monthly entries before exchange fallback.
        full_tick = native_monthly.ffill().reindex(pd.DatetimeIndex(frame.date), method="ffill").fillna(fallback)
        full = pd.Series(full_tick.to_numpy(), index=frame.date)
        past = pd.Series(past_tick.to_numpy(), index=frame.date)
        target = frame.date.between(pd.Timestamp(plan["start"]), pd.Timestamp(plan["comparison_end"]))
        differences = target.to_numpy() & (full.to_numpy() != past.to_numpy())
        count, affected = 0, 0
        for row in orders_by_pair[pair]:
            at = row["at"]
            assert at in full.index
            a, b = float(full.loc[at]), float(past.loc[at])
            count += 1
            if a != b:
                affected += 1
                price = D(str(row["filled_price"]))
                off_grid = price % D(str(b)) != 0
                fills_differ += int(off_grid)
                findings.append({**row, "at": at.isoformat(), "full_month_tick": a,
                    "strict_past_observed_tick": b, "recorded_fill_off_past_grid": bool(off_grid),
                    "not_a_counterfactual_replay": True})
        first = frame.loc[differences, "date"]
        summaries.append({"pair": pair, "native_loaded_rows": len(frame),
            "target_bars_with_different_empirical_precision": int(differences.sum()),
            "first_difference": first.iloc[0].isoformat() if len(first) else None,
            "recorded_native_orders_checked": count, "recorded_orders_at_different_precision": affected})
        bars += int(target.sum())
        mismatches += int(differences.sum())
        inspected += count
        print(pair, "bars", int(differences.sum()), "order clocks", affected, flush=True)
        del frame, prefix_count, past_tick, full_tick, full, past
        gc.collect()
    result = {"passed_execution": True, "native_algorithm_calls": len(summaries), "resolved_startup_candles": startup,
        "native_log_hashes": log_hashes,
        "pairs": summaries, "target_bar_observations": bars,
        "target_bars_with_full_vs_past_precision_difference": mismatches,
        "recorded_order_observations_checked": inspected,
        "recorded_orders_at_different_precision": len(findings),
        "recorded_fills_off_strict_past_grid": fills_differ,
        "findings": findings, "exchange_historical_tick_certified": False,
        "causal_full_month_precision_proven": mismatches == 0,
        "primary_financial_runs": 0, "corrected_account_returns": None,
        "scope": "Original native monthly heuristic versus known completed OHLC; not actual exchange rules",
        "orders_enabled": False, "production_eligible": False, "goal_complete": False}
    (HERE / "precision_audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print("Precision audit complete", mismatches, "bars;", len(findings), "order clocks", flush=True)


if __name__ == "__main__":
    main()
