"""Check native multitimeframe indicators and entries using training-only prefixes."""

import argparse
import gc
import json
import os
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.commands.optimize_commands import setup_optimize_configuration
from freqtrade.enums import RunMode
from freqtrade.exchange import timeframe_to_minutes
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.research.data import read, save, sha
from freqtrade.research.workflow import source_identity
from research.crypto.run_candidate import native_stats


def training_cases(trades):
    ordered = sorted(trades, key=lambda t: (t["open_timestamp"], t["pair"]))
    selected, tags = [], set()
    for trade in ordered:
        if trade["enter_tag"].strip() not in tags:
            selected.append(trade)
            tags.add(trade["enter_tag"].strip())
    rebuy = [t for t in ordered if set(t["enter_tag"].split()) == {"61"}]
    for i in np.linspace(0, len(rebuy) - 1, min(6, len(rebuy))).round().astype(int):
        if rebuy[i] not in selected:
            selected.append(rebuy[i])
    for i in np.linspace(0, len(ordered) - 1, min(20, len(ordered))).round().astype(int):
        if len(selected) >= 20:
            break
        if ordered[i] not in selected:
            selected.append(ordered[i])
    for trade in ordered:
        if len(selected) >= 20:
            break
        if trade not in selected:
            selected.append(trade)
    if len(selected) < 20:
        raise ValueError("Twenty training entries are required")
    return selected[:20]


def compare_prefix(full, cut, signal, trade, step):
    begin = signal - 12 * step
    a = full.loc[full.date.between(begin, signal)].reset_index(drop=True)
    b = cut.loc[cut.date.between(begin, signal)].reset_index(drop=True)
    dtype_differences = {
        name: [str(a[name].dtype), str(b[name].dtype)]
        for name in a.columns.intersection(b.columns)
        if a[name].dtype != b[name].dtype
    }
    error = None
    try:
        pd.testing.assert_frame_equal(a, b, check_exact=True, check_dtype=False)
        last = a.loc[a.date.eq(signal)].iloc[0]
        direction = "enter_short" if trade["is_short"] else "enter_long"
        if not last[direction] == 1 or not set(trade["enter_tag"].split()).issubset(
            set(last["enter_tag"].split())
        ):
            raise AssertionError("Original entry signal was not reproduced")
    except (AssertionError, IndexError) as exc:
        error = str(exc)[:2000]
    return len(a), len(a.columns), error, dtype_differences


def check(run: Path, output: Path):
    if any(key.startswith("FREQTRADE__") for key in os.environ):
        raise ValueError("Unexpected configuration environment override")
    chosen = read(run / "selection.json")["agent"]
    if not chosen or chosen == "cash":
        raise ValueError("No selected candidate")
    reference = run / "train" / chosen / "cost_3bps"
    plan = read(run / "plan.json")
    step = pd.Timedelta(minutes=timeframe_to_minutes(plan["timeframe"]))
    if read(reference / "freeze.json")["source_identity"] != source_identity():
        raise ValueError("Native source changed")
    for record in plan["strategy_sources"]:
        if sha(reference / "strategies" / Path(record["path"]).name) != record["sha256"]:
            raise ValueError("Frozen strategy source changed")
    output.mkdir(exist_ok=False)
    cases = training_cases(native_stats(reference)["trades"])
    save(output / "cases.json", cases)
    grouped = defaultdict(list)
    for index, trade in enumerate(cases):
        grouped[trade["pair"]].append((index, trade))
    shared_exchange = None

    def frame(pair, label, until=None):
        nonlocal shared_exchange
        folder = output / label
        folder.mkdir()
        settings = read(reference / "settings.json")
        settings.update(user_data_dir=str(folder), exportdirectory=str(folder))
        settings["exchange"]["pair_whitelist"] = [pair]
        if until is not None:
            lower = settings["timerange"].split("-")[0]
            settings["timerange"] = f"{lower}-{int(until.timestamp()) - 1}"
        save(folder / "settings.json", settings)
        config = setup_optimize_configuration(
            {
                "config": [str(folder / "settings.json")],
                "datadir": str(Path(plan["dataset"]) / "candles"),
            },
            RunMode.BACKTEST,
        )
        engine = Backtesting(config, shared_exchange)
        shared_exchange = engine.exchange
        engine._set_strategy(engine.strategylist[0])
        data, _ = engine.load_bt_data()
        indicators = engine.strategy.advise_all_indicators(data)[pair]
        result = engine.strategy.ft_advise_signals(indicators, {"pair": pair})
        del engine, data, indicators
        gc.collect()
        return result

    rows = []
    try:
        for pair, pair_cases in grouped.items():
            full = frame(pair, f"full_{pair.split('/')[0]}")
            for index, trade in pair_cases:
                signal = pd.Timestamp(trade["open_date"]) - step
                cut = frame(pair, f"case_{index:02}", signal + step)
                count, columns, error, dtypes = compare_prefix(full, cut, signal, trade, step)
                row = {
                    "case": index,
                    "pair": pair,
                    "signal": signal.isoformat(),
                    "tag": trade["enter_tag"].strip(),
                    "is_short": trade["is_short"],
                    "rows_compared": count,
                    "columns_compared": columns,
                    "passed": error is None,
                    "error": error,
                    "storage_dtype_differences": dtypes,
                }
                rows.append(row)
                save(output / f"case_{index:02}.json", row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
                del cut
                gc.collect()
            del full
            gc.collect()
    finally:
        if shared_exchange is not None:
            shared_exchange.close()
    summary = {
        "passed": len(rows) == 20 and all(row["passed"] for row in rows),
        "cases": sorted(rows, key=lambda row: row["case"]),
        "pure_rebuy_cases": sum(set(row["tag"].split()) == {"61"} for row in rows),
        "scope": (
            "native indicators and entry tags, exact last12-bar prefix equality; "
            "same registered slot configuration and native informative candles; "
            "does not test portfolio admission timing, exits or DCA lifecycle"
        ),
        "worker_sha256": sha(Path(__file__)),
        "orders_enabled": False,
    }
    save(output / "result.json", summary)
    save(run / "prefix_result.json", dict(summary, output=str(output)))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(check(args.run.resolve(), args.output.resolve()), ensure_ascii=False, indent=2)
    )
