"""Freeze small synthetic1m inputs and explicit matching policies before runs."""

import copy
import hashlib
import json
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/suspension_native_20261117_v1"
PAIRS = ["FAKEONE/USDT:USDT", "FAKETWO/USDT:USDT"]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    Path(path).write_text(json.dumps(data, indent=2) + "\n")


def main():
    OUT.mkdir(exist_ok=False)
    old = ROOT / "user_data/crypto_quant/copula_fills_native_20261115_v4/fixtures/normal1000"
    base_config = json.loads((old / "config.json").read_text())
    metadata = json.loads((old / "synthetic_markets_maintenance_fixed.json").read_text())
    for pair in PAIRS:
        metadata["markets"][pair]["precision"]["price"] = 0.001
    start, end = pd.Timestamp("2026-04-29T05:30Z"), pd.Timestamp("2026-04-29T06:30Z")
    cases = []
    for case, policy in (
        ("continue", "continue"),
        ("stop", "stop"),
        ("unknown", "unknown"),
        ("missing_detail", "continue"),
    ):
        folder = OUT / "inputs" / case
        data = folder / "candles/futures"
        data.mkdir(parents=True)
        for pair in PAIRS:
            stem = pair.replace("/", "_").replace(":", "_")
            dates = pd.date_range(start, end, freq="min", inclusive="left")
            detail = pd.DataFrame(
                {
                    "date": dates,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1000.0,
                }
            )
            if pair == PAIRS[0]:
                detail.loc[detail.date == pd.Timestamp("2026-04-29T06:00Z"), "high"] = 104.0
                detail.loc[detail.date == pd.Timestamp("2026-04-29T06:02Z"), "high"] = 104.0
            main = (
                detail.set_index("date")
                .resample("5min")
                .agg(
                    {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
                )
                .reset_index()
            )
            main.to_feather(data / f"{stem}-5m-futures.feather")
            if case == "missing_detail" and pair == PAIRS[0]:
                detail = detail.loc[detail.date != pd.Timestamp("2026-04-29T06:01Z")]
            detail.reset_index(drop=True).to_feather(data / f"{stem}-1m-futures.feather")
            hours = pd.date_range("2026-04-29T05:00Z", "2026-04-29T07:00Z", freq="h")
            marks = pd.DataFrame(
                {
                    "date": hours,
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 0.0,
                }
            )
            marks.to_feather(data / f"{stem}-1h-mark.feather")
            funding = marks.copy()
            funding[["open", "high", "low", "close"]] = 0.001
            funding.to_feather(data / f"{stem}-1h-funding_rate.feather")
        raw = copy.deepcopy(base_config)
        for key in (
            "fixture_forecasts",
            "fixture_forecasts_sha256",
            "copula_helper_sha256",
            "research_decision_end",
        ):
            raw.pop(key, None)
        raw.update(
            strategy="FixtureOperations",
            strategy_path=str(HERE),
            timeframe="5m",
            timeframe_detail="1m",
            startup_candle_count=0,
            margin_mode="isolated",
            dry_run=True,
            dry_run_wallet=1000,
            stake_amount=100,
            max_open_trades=2,
            research_dated_price_rules=True,
            cache="none",
            backtest_cache="none",
            timerange=f"{int(start.timestamp())}-{int(end.timestamp()) - 1}",
        )
        raw["unfilledtimeout"].update(entry=30, exit=30)
        raw["exchange"]["pair_whitelist"] = PAIRS
        save(folder / "config.json", raw)
        save(folder / "markets.json", metadata)
        cases.append(
            {
                "name": case,
                "policy": policy,
                "folder": str(folder),
                "inputs": {str(p): sha(p) for p in sorted(folder.rglob("*")) if p.is_file()},
            }
        )
    save(
        HERE / "fixture_plan.json",
        {
            "scope": "Synthetic fixtures only;no historical alpha",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "operation_start": "2026-04-29T06:00:00+00:00",
            "operation_end": "2026-04-29T06:01:00+00:00",
            "pairs": PAIRS,
            "cases": cases,
            "source_hashes": {
                str(p): sha(p)
                for p in (
                    Path(__file__),
                    HERE / "FixtureOperations.py",
                    ROOT / "freqtrade/optimize/order_operations.py",
                    ROOT / "freqtrade/optimize/dated_price_precision.py",
                    ROOT / "freqtrade/optimize/backtesting.py",
                )
            },
            "expected": {
                "continue": "old_exit06:00;new_entry06:01;funding_retained",
                "stop": "old_exit06:02;new_entry06:01;funding_retained",
                "unknown": "reject_reachable_pending_match",
                "missing_detail": "reject_raw_gap_even_if_native_gap_fills",
            },
            "orders_enabled": False,
            "production_eligible": False,
        },
    )
    print("Frozen4native fixtures before lifecycle outputs")


if __name__ == "__main__":
    main()
