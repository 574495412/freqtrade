"""Register and run the November extension through the existing Freqtrade chain."""

import argparse
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .continuous_native_inputs import IndependentShardRead
from .continuous_november_inputs import PROTOCOL, ROOT
from .continuous_run import COSTS, ROOT_RUN, SLOTS, frozen_roles, launch, register, report_continuous
from .continuous_seams import INTERVALS
from .inputs import HERE, OUT


RUNS = OUT / "continuous_native_november_replays_20261126"
PROGRESS = HERE / "continuous_native_november_wallet_progress.json"


def plans():
    inputs = receipt(ROOT / "shards.json")
    configs = {}
    for cost in COSTS:
        for slots, origin in frozen_roles().items():
            target = RUNS / f"full_{slots}_{cost}bp"
            configs[(slots, cost)] = register(origin, inputs, target / "registration",
                                               is_witness=False, wallet_protocol=PROTOCOL)
    return inputs, configs


def verify_native_seam(runner, configuration):
    plan = read(Path(configuration))
    source = plan["data_receipts"]
    native = runpy.run_path(runner)
    engine = native["verify"](plan, plan["candidates"][0])
    from freqtrade import __file__ as loaded_engine
    from freqtrade.configuration import TimeRange
    from freqtrade.enums import CandleType
    from .sharded_feather import ShardedFeatherDataHandler

    assert Path(loaded_engine).resolve() == engine / "freqtrade/__init__.py", loaded_engine
    handler = ShardedFeatherDataHandler(ROOT, source)
    independent = IndependentShardRead(source)
    seam = pd.Timestamp(read(PROTOCOL)["seam_utc"])
    start, stop = seam - pd.Timedelta(days=2), seam + pd.Timedelta(days=2)
    timerange = TimeRange("date", "date", int(start.timestamp()), int(stop.timestamp()))
    verified = {}
    for pair in ("BTC/USDT:USDT", "BNT/USDT:USDT", "WLD/USDT:USDT"):
        verified[pair] = {}
        for kind in INTERVALS:
            timeframe, candle = kind.split("-", 1)
            observed = handler._ohlcv_load(pair, timeframe, timerange, CandleType(candle))
            oracle = independent(pair, kind)
            columns = list(oracle.columns)
            observed = observed.loc[observed.date.between(start, stop, inclusive="left"), columns].reset_index(drop=True)
            oracle = oracle.loc[oracle.date.between(start, stop, inclusive="left")].reset_index(drop=True)
            observed["date"] = observed.date.dt.as_unit("ns")
            oracle["date"] = oracle.date.dt.as_unit("ns")
            pd.testing.assert_frame_equal(observed, oracle, check_exact=True, check_dtype=False)
            assert observed.date.min() < seam <= observed.date.max()
            verified[pair][kind] = len(observed)
    return verified


def preflight():
    source, configs = plans()
    runner, configuration = configs[(12, 15)]
    plan = read(checked(configuration))
    # Enter the committed engine before importing any workspace research modules.
    bootstrap = (
        "import json,sys; sys.path.insert(0,sys.argv[1]);"
        "from research.crypto.nfi80_extended_windows_20261126_v1.continuous_november_run import verify_native_seam;"
        "print(json.dumps(verify_native_seam(*sys.argv[2:])))"
    )
    command = [sys.executable, "-B", "-c", bootstrap, plan["engine_source"],
               str(checked(runner)), str(checked(configuration))]
    verified = json.loads(subprocess.check_output(command, text=True))
    plan_refs = {f"{slots}_{cost}bp": configuration for (slots, cost), (_, configuration) in configs.items()}
    return write_once(HERE / "continuous_november_source_gate.json", {
        "manifest": source, "native_engine_git_head": plan["engine_commit"], "plans": plan_refs,
        "native_boundary_reads": verified, "checked_full_native_runner_and_sources": True,
        "source_schedule_days": 1060, "expected_five_minute_marks": 305280,
        "continuous_accounts_completed": 0, "orders_enabled": False,
    })


def run():
    _, configs = plans()
    gate = read(HERE / "continuous_november_source_gate.json")
    witness = read(ROOT_RUN / "witness/matched.json")
    assert witness["exact_fills"] and witness["exact_full_marked_wallet"]
    for cost in COSTS:
        for slots in SLOTS:
            registration = configs[(slots, cost)]
            assert gate["plans"][f"{slots}_{cost}bp"] == registration[1]
            launch(registration, RUNS / f"full_{slots}_{cost}bp", cost)
            report_continuous(ROOT, RUNS, PROGRESS)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("mode", choices=("register", "preflight", "run", "report"))
    mode = cli.parse_args().mode
    if mode == "register":
        _, cases = plans()
        print({key: config["path"] for key, (_, config) in cases.items()})
    elif mode == "preflight":
        print(preflight())
    elif mode == "run":
        run()
    else:
        report_continuous(ROOT, RUNS, PROGRESS)
