"""Observe unchanged official lookahead and controlled native prefix accounts."""

import argparse
import copy
import gc
import hashlib
import json
import os
import shutil
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
ENGINE = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")
sys.path.insert(0, str(ENGINE))

import pandas as pd  # noqa: E402
import psutil  # noqa: E402

import freqtrade  # noqa: E402
from freqtrade.main import main as official_main  # noqa: E402
from freqtrade.optimize.analysis.base_analysis import VarHolder  # noqa: E402
from freqtrade.optimize.analysis.lookahead import LookaheadAnalysis  # noqa: E402
from freqtrade.optimize.backtesting import Backtesting  # noqa: E402
from freqtrade.persistence import LocalTrade  # noqa: E402


TARGETS = {
    "futures": [
        ("ADA/USDT:USDT", "2025-01-19T22:50Z", "2025-01-20T03:05Z"),
        ("ADA/USDT:USDT", "2025-01-20T17:40Z", "2025-01-20T21:50Z"),
        ("ETH/USDT:USDT", "2025-01-27T16:40Z", "2025-02-02T00:45Z"),
    ],
    "spot": [
        ("ADA/USDT", "2025-01-19T22:50Z", "2025-01-20T06:45Z"),
        ("ADA/USDT", "2025-01-20T17:40Z", "2025-01-20T21:50Z"),
        ("ETH/USDT", "2025-02-28T02:35Z", "2025-03-01T06:30Z"),
    ],
}


def read(p):
    return json.loads(Path(p).read_text())


def save(p, value):
    Path(p).write_text(json.dumps(value, indent=2, default=str) + "\n")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def caches(directory, destination):
    destination.mkdir(exist_ok=True)
    for path in Path(directory).glob("nfix8-profit_max-*.json"):
        shutil.copy2(path, destination / path.name)


def strict_target(records, pair, opened, closed):
    return [
        r
        for r in records
        if r["pair"] == pair
        and pd.Timestamp(r["open_date"]) == pd.Timestamp(opened)
        and pd.Timestamp(r["close_date"]) == pd.Timestamp(closed)
        and r["exit_reason"] != "force_exit"
    ]


def main(market, output):  # noqa: C901 -- serialized observation phases
    if Path(freqtrade.__file__).resolve() != ENGINE / "freqtrade/__init__.py":
        raise ValueError("Wrong pinned native package")
    manifest = read(ENGINE.parent / "FREQTRADE_SOURCE.json")
    if manifest["commit"] != "c051e9065c5b677f03817dd1f83cb980af1ed684":
        raise ValueError("Wrong native engine version")
    for name, digest in manifest["files"].items():
        if sha(ENGINE / name) != digest:
            raise ValueError("Native engine bytes changed")
    prior = (
        ROOT
        / "user_data/crypto_quant"
        / (
            "nfi_x8_lookahead_check_20261105_v001"
            if market == "futures"
            else "nfi_x8_spot_lookahead_check_20261105_v001"
        )
    )
    command = read(prior / "process.json")["command"][1:]
    source_config = Path(command[command.index("--config") + 1])
    config = read(source_config)
    if not config["dry_run"] or any(
        config["exchange"].get(k) for k in ("key", "secret", "password")
    ):
        raise ValueError("Anonymous diagnostic configuration required")
    output.mkdir(parents=True, exist_ok=False)
    official_dir = output / "official"
    official_dir.mkdir()
    config["api_server"]["enabled"] = False
    config["telegram"]["enabled"] = False
    if "webhook" in config:
        config["webhook"]["enabled"] = False
    save(output / "config.json", config)
    for flag, value in (
        ("--config", output / "config.json"),
        ("--userdir", official_dir),
        ("--lookahead-analysis-exportfilename", output / "official.csv"),
    ):
        command[command.index(flag) + 1] = str(value)
    save(
        output / "command.json",
        {
            "command": command,
            "synthetic_diagnostic_not_account_performance": True,
            "orders_enabled": False,
        },
    )
    started = time.monotonic()

    def guard():
        while True:
            rss = psutil.Process().memory_info().rss
            if (
                rss > 5.5 * 1024**3
                or shutil.disk_usage(output).free < 1.5 * 1024**3
                or time.monotonic() - started > 1800
            ):
                save(
                    output / "resource-guard.json",
                    {"rss": rss, "elapsed": time.monotonic() - started},
                )
                os._exit(73)
            time.sleep(2)

    threading.Thread(target=guard, daemon=True).start()
    calls = []
    current = {}
    observed = {}
    prepare = LookaheadAnalysis.prepare_data
    analyze = LookaheadAnalysis.analyze_row
    set_strategy = Backtesting._set_strategy

    def traced_strategy(account, strategy):
        set_strategy(account, strategy)
        s = account.strategy
        for name in ("bot_loop_start", "confirm_trade_entry", "custom_exit"):
            original = getattr(s, name)

            def callback(
                *args, original=original, name=name, strategy=s, account=account, **kwargs
            ):
                result = original(*args, **kwargs)
                clock = kwargs.get("current_time")
                if clock is None and name == "custom_exit" and len(args) > 2:
                    clock = args[2]
                if clock is not None and any(
                    abs((pd.Timestamp(clock) - pd.Timestamp(t[2])).total_seconds()) <= 1800
                    for t in TARGETS[market]
                ):
                    state = [
                        {
                            "pair": t.pair,
                            "opened": str(t.open_date),
                            "stake": t.stake_amount,
                            "amount": t.amount,
                            "tag": t.enter_tag,
                            "entries": t.nr_of_successful_entries,
                        }
                        for t in LocalTrade.bt_trades_open
                    ]
                    record = {
                        "callback": name,
                        "clock": str(clock),
                        "pair": kwargs.get("pair", args[0] if args else None),
                        "result": result,
                        "positions": state,
                        "closed_pnl": LocalTrade.bt_total_profit,
                        "profit_cache": copy.deepcopy(strategy.target_profit_cache.data),
                    }
                    with (current["folder"] / "callback-state.jsonl").open("a") as f:
                        f.write(json.dumps(record, default=str) + "\n")
                return result

            setattr(s, name, callback)

    def observed_prepare(analysis, holder, pairs):
        case = output / f"call-{len(calls):03d}"
        case.mkdir()
        current["folder"] = case
        kind = getattr(
            holder,
            "_audit_kind",
            "full"
            if holder is analysis.full_varHolder
            else "entry"
            if any(holder is h for h in analysis.entry_varHolders)
            else "exit",
        )
        record = {
            "index": len(calls),
            "kind": kind,
            "pairs": pairs,
            "start": holder.from_dt.isoformat(),
            "end": holder.to_dt.isoformat(),
            "compared": str(getattr(holder, "compared_dt", "")),
            "folder": str(case),
        }
        calls.append(record)
        caches(analysis.local_config["user_data_dir"], case / "cache-before")
        prepare(analysis, holder, pairs)
        caches(analysis.local_config["user_data_dir"], case / "cache-after")
        holder.result["results"].to_json(case / "trades.json", orient="records", date_format="iso")
        comparisons = {}
        if holder is not analysis.full_varHolder:
            for pair, frame in holder.indicators.items():
                full = analysis.full_varHolder.indicators[pair].set_index("date")
                cut = frame.set_index("date")
                common = full.index.intersection(cut.index)
                cols = full.columns.intersection(cut.columns)
                diff = full.loc[common, cols].compare(cut.loc[common, cols])
                comparisons[pair] = {
                    "compared_rows": len(common),
                    "compared_columns": len(cols),
                    "different_rows": len(diff),
                    "different_columns": sorted(set(diff.columns.get_level_values(0))),
                }
        record.update(trade_records=len(holder.result["results"]), indicator_comparison=comparisons)
        save(case / "record.json", record)
        save(output / "calls.json", calls)
        observed["analysis"] = analysis

    def analyzed_row(analysis, idx, row):
        analyze(analysis, idx, row)
        # Original tool never revisits completed prefix holders, only their count.
        for holders in (analysis.entry_varHolders, analysis.exit_varHolders):
            if idx < len(holders):
                holders[idx].data, holders[idx].indicators, holders[idx].result = {}, {}, {}
        gc.collect()

    Backtesting._set_strategy = traced_strategy
    LookaheadAnalysis.prepare_data = observed_prepare
    LookaheadAnalysis.analyze_row = analyzed_row
    try:
        official_main(command)
    except SystemExit as exc:
        official_exit = 0 if exc.code is None else exc.code
    if official_exit != 0 or not (output / "official.csv").exists():
        raise RuntimeError("Official native lookahead did not complete")
    analysis = observed["analysis"]
    baseline = read(Path(calls[0]["folder"]) / "trades.json")
    original_calls = calls.copy()
    matrix = []
    for pair, opened, closed in TARGETS[market]:
        original_exit = next(
            (
                c
                for c in original_calls
                if c["kind"] == "exit"
                and pair in c["pairs"]
                and pd.Timestamp(c["compared"]) == pd.Timestamp(closed)
            ),
            None,
        )
        expected = strict_target(baseline, pair, opened, closed)
        if not expected or original_exit is None:
            matrix.append(
                {
                    "pair": pair,
                    "opened": opened,
                    "closed": closed,
                    "status": "ORIGINAL_TARGET_NOT_REPRODUCED",
                }
            )
            continue
        for scope in ("single", "all"):
            for seed in ("clean", "recorded"):
                userdir = output / f"matrix-{len(matrix):02d}"
                userdir.mkdir()
                if seed == "recorded":
                    for p in (Path(original_exit["folder"]) / "cache-before").glob("*.json"):
                        shutil.copy2(p, userdir / p.name)
                analysis.local_config["user_data_dir"] = userdir
                holder = VarHolder()
                holder.from_dt = analysis.full_varHolder.from_dt
                holder.to_dt = pd.Timestamp(closed).to_pydatetime() + timedelta(minutes=5)
                holder.compared_dt = pd.Timestamp(closed).to_pydatetime()
                holder._audit_kind = f"matrix-{scope}-{seed}"
                pairs = [pair] if scope == "single" else analysis.local_config["pairs"]
                observed_prepare(analysis, holder, pairs)
                result = read(current["folder"] / "trades.json")
                matches = strict_target(result, pair, opened, closed)
                matrix.append(
                    {
                        "pair": pair,
                        "opened": opened,
                        "closed": closed,
                        "scope": scope,
                        "cache": seed,
                        "target_natural_exit_reproduced": bool(matches),
                        "same_exit_reason": bool(
                            matches and matches[0]["exit_reason"] == expected[0]["exit_reason"]
                        ),
                        "expected_exit_reason": expected[0]["exit_reason"],
                        "matching_entry_campaigns": [
                            r
                            for r in result
                            if r["pair"] == pair
                            and pd.Timestamp(r["open_date"]) == pd.Timestamp(opened)
                        ],
                        "native_call": calls[-1],
                    }
                )
                holder.data, holder.indicators, holder.result = {}, {}, {}
                gc.collect()
    save(output / "matrix.json", matrix)
    for name, digest in manifest["files"].items():
        if sha(ENGINE / name) != digest:
            raise ValueError("Native engine changed during diagnostic")
    save(
        output / "completion.json",
        {
            "official_cli_exit_code": official_exit,
            "matrix_completed": True,
            "native_prepare_calls": len(calls),
            "elapsed_seconds": time.monotonic() - started,
            "engine_commit": manifest["commit"],
            "orders_enabled": False,
            "synthetic_diagnostic_only": True,
        },
    )
    print(
        json.dumps(
            {
                "market": market,
                "official": pd.read_csv(output / "official.csv").to_dict("records"),
                "matrix_rows": len(matrix),
                "seconds": time.monotonic() - started,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=TARGETS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    main(args.market, args.output)
