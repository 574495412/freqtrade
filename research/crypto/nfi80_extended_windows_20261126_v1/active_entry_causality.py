"""Check capped NFI entry rows from real longs and shorts at closed-candle cutoffs."""

import argparse
import json
import shutil
import subprocess
import sys
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .annual_trade_scale import FIVE_MINUTES
from .continuous_native_inputs import NativeShardBinding
from .inputs import HERE
from .native_trade_path_review import books


SIGNALS = ("enter_long", "enter_short", "exit_long", "exit_short", "enter_tag", "exit_tag")
OBSERVED_CLOCK = pd.Timestamp("2026-08-14T07:45:00Z")


def scalar(value):
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float) and not isfinite(value):
        return str(value)
    return value


def registered_samples():
    control = receipt(HERE / "recent_two_year_15bp_14_verified.json")
    prior = read(checked(control))["account"]
    account, native = books(read(checked(prior["proof"])))
    winners = [
        key for key in account["trades"]
        if not key[1] and key[2].year == 2025 and native[key]["enter_tag"].split() == ["145"]
    ]
    best = max(winners, key=lambda key: account["trades"][key]["profit"]
               / account["trades"][key]["equity_before_entry"])
    selected = {"2025_top_coins_winner": best,
                "2026_near_short_rebuy": ("NEAR/USDT:USDT", True, OBSERVED_CLOCK)}
    candidate = receipt(HERE / "short_rebuy_14_wallet_preflight.json")
    plan = read(checked(candidate))["by_cost"]["15"]["plan"]
    return {
        "implementation": receipt(Path(__file__)),
        "observed_control": control,
        "candidate_registration": candidate,
        "candidate_plan": plan,
        "samples": {
            name: {
                "pair": key[0], "short": key[1], "first_fill": key[2].isoformat(),
                "expected_entry_tags": native[key]["enter_tag"].split(),
            }
            for name, key in selected.items()
        },
        "comparison": (
            "Reuse the original warmup anchor. Compare the row ending at the actual first fill "
            "with inputs available then and with seven additional days. Use the native candle "
            "provider's closed-informative timeframe boundary in both phases."
        ),
        "orders_enabled": False,
    }


def diagnostic_phase(plan, case, cutoff):
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.enums import CandleType, RunMode
    from freqtrade.resolvers import StrategyResolver

    candidate, = plan["candidates"]
    fill = pd.Timestamp(case["first_fill"])
    schedule = read(checked(plan["historical_schedule"]))["days"]
    admitted = schedule[fill.strftime("%Y-%m-%d")]["pairs"]
    if case["pair"] not in admitted:
        raise ValueError("Native entry is absent from the registered UTC universe")
    with TemporaryDirectory(prefix="nfi-active-entry-") as folder:
        path = Path(folder)
        for filename, source in candidate["source_files"].items():
            shutil.copyfile(checked(source), path / filename)
        config = read(checked(candidate["config"]))
        start = int(pd.Timestamp(plan["start"]).timestamp())
        config.update(
            timerange=f"{start}-{int(cutoff.timestamp())}",
            datadir=Path(plan["datadir"]),
            user_data_dir=path,
            strategy_path=folder,
            runmode=RunMode.BACKTEST,
            candle_type_def=CandleType.FUTURES,
        )
        if not config["dry_run"]:
            raise ValueError("Only frozen historical strategy resolution is permitted")
        strategy = StrategyResolver.load_strategy(config)
        binding = NativeShardBinding(plan, config["timeframe"])
        binding.install()
        try:
            provider = DataProvider(config, None, pairlists=SimpleNamespace(whitelist=admitted))
            binding.bind_provider(provider)
            provider._set_dataframe_max_date(cutoff)
            strategy.dp = provider
            strategy.ft_bot_start()
            frame = provider.get_pair_dataframe(case["pair"], config["timeframe"])
            target = fill - FIVE_MINUTES
            if frame.date.iloc[-1] != cutoff - FIVE_MINUTES or not frame.date.eq(target).any():
                raise ValueError("Unexpected native cutoff or missing recorded signal candle")
            computed = strategy.advise_all_indicators({case["pair"]: frame})[case["pair"]]
            analyzed = strategy.ft_advise_signals(computed, {"pair": case["pair"]})
            selected = analyzed.loc[analyzed.date == target]
            if len(selected) != 1 or not binding.handler.checked_parts:
                raise ValueError("Native active row was not uniquely observed")
            snapshot = {name: scalar(value) for name, value in selected.iloc[0].to_dict().items()}
            return {"evaluated_rows": len(frame), "cutoff_utc": cutoff.isoformat(),
                    "input_files_checked": len(binding.handler.checked_parts),
                    "snapshot": snapshot}
        finally:
            binding.history.load_data = binding.source_load
            binding.history_utils.get_datahandler = binding.source_handler


def evaluate(frozen, name, days):
    from freqtrade import __file__ as engine_file

    plan = read(checked(frozen["candidate_plan"]))
    if Path(engine_file).resolve() != Path(plan["engine_source"]) / "freqtrade/__init__.py":
        raise ValueError("Wrong native Freqtrade code selected")
    case = frozen["samples"][name]
    cut = pd.Timestamp(case["first_fill"]) + pd.Timedelta(days=days)
    with patch("socket.socket.connect", side_effect=RuntimeError("Frozen analysis is offline")):
        return diagnostic_phase(plan, case, cut)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", nargs=3, metavar=("FREEZE", "CASE", "DAYS"))
    args = parser.parse_args()
    if args.phase:
        frozen, name, days = args.phase
        print(json.dumps(evaluate(read(Path(frozen)), name, int(days)), allow_nan=False))
        return
    frozen = registered_samples()
    freeze = write_once(HERE / "active_entry_causality_freeze.json", frozen)
    engine = read(checked(frozen["candidate_plan"]))["engine_source"]
    boot = (
        "import runpy,sys; sys.path.insert(0,sys.argv.pop(1));"
        "runpy.run_module(sys.argv.pop(1),run_name='__main__')"
    )
    anchor = "research.crypto.nfi80_extended_windows_20261126_v1.active_entry_causality"
    results = {}
    for name, case in frozen["samples"].items():
        rows = [
            json.loads(subprocess.check_output(
                [sys.executable, "-B", "-c", boot, engine, anchor, "--phase",
                 str(checked(freeze)), name, str(days)],
                text=True,
            ))
            for days in (0, 7)
        ]
        left, right = [phase["snapshot"] for phase in rows]
        changed = sorted(key for key in left.keys() | right.keys()
                         if left.get(key) != right.get(key) or (key in left) != (key in right))
        field = "enter_short" if case["short"] else "enter_long"
        recorded = left[field] == 1 and left["enter_tag"].split() == case["expected_entry_tags"]
        results[name] = {
            "sample": case, "identical_signal_values": not set(changed).intersection(SIGNALS),
            "recorded_entry_reproduced": recorded, "changed_columns": changed,
            "phases": rows,
        }
        print(name, recorded, changed, flush=True)
    write_once(HERE / "active_entry_causality.json", {
        "freeze": freeze, "checks": results, "new_native_wallet_accounts": 0,
        "orders_enabled": False, "production_eligible": False,
    })


if __name__ == "__main__":
    main()
