"""Run the registered continuous accounts using native Freqtrade candle consumers."""

import argparse
from fcntl import LOCK_EX, flock
from pathlib import Path
import subprocess
import sys

import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import LOCK, ROOT, checked, read, receipt, save
from research.crypto.nfi80_online_baseline_20261125_v1 import audit

from .accounts import COSTS, remove_completed_cache, write_once
from .annual_trade_scale import describe, load_account
from .continuous_native_inputs import IndependentShardRead
from .continuous_shards import ROOT as INPUTS
from .inputs import HERE, OUT
from .low_disk_run import render_runner


ROOT_RUN = OUT / "continuous_native_replays_20261126"
SLOTS = (12, 14)
WITNESS_BOUNDARY = "2026-11-18T00:00:00+00:00"
WALLET_PROTOCOL = HERE / "continuous_wallet_protocol.json"


def render_bound(parent, directory):
    baseline = render_runner(parent, directory / "original_bounded_renderer.py")
    bound = checked(baseline).read_text()

    def replace(before, after):
        nonlocal bound
        if bound.count(before) != 1:
            raise ValueError("Pinned native runner source does not match the input seam")
        bound = bound.replace(before, after, 1)

    replace(
        '    receipts = json.loads(checked(plan["data_receipts"]).read_text())\n'
        '    for name, digest in receipts.items():\n'
        '        if sha(Path(plan["datadir"]) / name) != digest:\n'
        '            raise ValueError("Historical input changed: " + name)',
        '    shards = json.loads(checked(plan["data_receipts"]).read_text())\n'
        '    if shards["schedule"] != plan["historical_schedule"] or shards["engine_commit"] != plan["engine_commit"]:\n'
        '        raise ValueError("Shard schedule differs from registered account")\n'
        '    for path, digest in { (part["path"], part["sha256"])\n'
        '        for slices in shards["files"].values() for part in slices }:\n'
        '        checked({"path": path, "sha256": digest})\n'
        '    checked(plan["shard_handler"])\n'
        '    checked(plan["native_input_binding"])',
    )
    replace(
        "    account = None\n    try:\n        account = native.Backtesting(config, exchange=exchange)",
        '    binding_module = load_module("native_continuous_binding", checked(plan["native_input_binding"]))\n'
        '    binding = binding_module.NativeShardBinding(plan, config["timeframe"])\n'
        '    binding.install()\n'
        '    account = None\n    try:\n        account = native.Backtesting(config, exchange=exchange)',
    )
    replace("        provider = account.dataprovider\n", "        provider = account.dataprovider\n"
            "        binding.bind_provider(provider)\n")
    replace("observer_module.Observer(account, observer_plan, output)",
            "binding.observer(observer_module.Observer, account, observer_plan, output)")
    replace('        save(output / "bounded_storage_audit.json", replay_storage.audit())',
            '        save(output / "bounded_storage_audit.json", replay_storage.audit())\n'
            '        save(output / "sharded_reads_audit.json", binding.audit())')
    path = directory / "runner.py"
    compile(bound, str(path), "exec")
    if path.exists():
        assert path.read_text() == bound
    else:
        with path.open("x") as handle:
            handle.write(bound)
    return receipt(path), baseline


def register(parent, shards, directory, *, is_witness, wallet_protocol=WALLET_PROTOCOL):
    directory.mkdir(parents=True, exist_ok=True)
    source = read(checked(parent))
    native_source, = source["candidates"]
    config = read(checked(native_source["config"]))
    inputs = read(checked(shards))
    schedule = read(checked(inputs["schedule"]))
    wallet = read(wallet_protocol)
    start, stop = (pd.Timestamp(inputs[key]) for key in ("first_time", "stop_exclusive"))
    last = stop - pd.Timedelta(minutes=5)
    if is_witness:
        assert source["historical_schedule"] == inputs["schedule"]
        assert inputs["first_time"] == source["start"] and inputs["stop_exclusive"] == source["end"]
    else:
        window = wallet["window"]
        assert inputs["first_time"] == window["start_utc"]
        assert inputs["stop_exclusive"] == window["end_exclusive_utc"]
        assert last == pd.Timestamp(window["last_mark_utc"])
        assert inputs["five_minute_marks_expected"] == window["five_minute_marks"] == (stop - start) / pd.Timedelta(minutes=5)
        calendar = pd.date_range(start, stop, freq="D", inclusive="left")
        assert list(schedule["days"]) == calendar.strftime("%Y-%m-%d").tolist()
        assert inputs["daily_decisions"] == window["pairlist_days"] == len(calendar)
    assert inputs["engine_commit"] == source["engine_commit"] and source["allowed_leverage"] == 3
    assert source["initial_equity_usdt"] == 1000 and config["dry_run"] is True
    assert config["margin_mode"] == "isolated" and not config.get("timeframe_detail")
    assert not any(config["exchange"].get(key) for key in ("key", "secret", "password"))
    for strategy_file in native_source["source_files"].values():
        checked(strategy_file)
    pairs = schedule["union"]
    config["exchange"] = {**config["exchange"], "pair_whitelist": pairs}
    listed, = config["pairlists"]
    assert listed["method"] == "HistoricalVolumePairList"
    config["pairlists"] = [{**listed, "schedule": inputs["schedule"]["path"],
                            "sha256": inputs["schedule"]["sha256"]}]
    revised_config = write_once(directory / "config.json", config)
    runner, original = render_bound(source, directory)
    periods = source["periods"]
    if not is_witness:
        year_boundaries = [pd.Timestamp(year=year, month=1, day=1, tz="UTC")
                           for year in range(start.year + 1, last.year + 1)]
        splits = [start, *(date for date in year_boundaries if date < last), last]
        periods = [{"name": "continuous", "start": start.isoformat(), "end": last.isoformat()}]
        periods.extend({"name": str(a.year), "start": a.isoformat(), "end": b.isoformat()}
                       for a, b in zip(splits, splits[1:]))
    case = {**native_source, "config": revised_config}
    plan = {
        **source, "candidates": [case], "start": start.isoformat(), "end": stop.isoformat(),
        "comparison_end": last.isoformat(), "periods": periods,
        "pairs": pairs, "data_receipts": shards, "historical_schedule": inputs["schedule"],
        "market_snapshot": inputs["market_snapshot"], "datadir": str(Path(shards["path"]).parent),
        "marks": {}, "runner_sha256": runner["sha256"],
        "storage_source": receipt(HERE / "low_disk_block_storage.py"),
        "row_storage": receipt(HERE / "low_disk_rows.py"),
        "native_input_binding": receipt(HERE / "continuous_native_inputs.py"),
        "shard_handler": receipt(HERE / "sharded_feather.py"),
        "native_registered_parent": parent, "unsharded_bounded_renderer": original,
        "independent_audit_source": receipt(Path(audit.__file__)),
        "continuous_wallet_protocol": receipt(wallet_protocol),
        "runtime_is_technical_witness": is_witness,
        "orders_enabled": False, "production_eligible": False,
    }
    return runner, write_once(directory / "plan.json", plan)


def frozen_roles():
    root = HERE.parent / "nfi80_online_baseline_20261125_v1"
    references = read(root / "baseline_alignment.json")["native_historical_reference"]
    paths = {12: references["plan"], 14: receipt(root / "plan.json")}
    controls = read(checked(paths[12]))["candidates"][0]
    wider = read(checked(paths[14]))["candidates"][0]
    assert (controls["max_open_trades"], wider["max_open_trades"]) == SLOTS
    assert (controls["strategy"], wider["strategy"]) == ("FlatTopBoost", "FlatBoostFourteenSlots")
    assert all(wider["source_files"][key]["sha256"] == val["sha256"]
               for key, val in controls["source_files"].items())
    control_cfg, wider_cfg = (read(checked(role["config"])) for role in (controls, wider))
    ignored = {"strategy", "max_open_trades"}
    assert {key: value for key, value in control_cfg.items() if key not in ignored} == {
        key: value for key, value in wider_cfg.items() if key not in ignored
    }
    return paths


def make_witness():
    source = receipt(OUT / "registrations/fixed_boost_12-plan.json")
    origin = read(checked(source))
    boundaries = [(None, WITNESS_BOUNDARY), (WITNESS_BOUNDARY, origin["end"])]
    entries = {}
    for name, digest in read(checked(origin["data_receipts"])).items():
        path = str(Path(origin["datadir"]) / name)
        entries[name] = [{"path": path, "sha256": digest, "start_inclusive": a, "stop_exclusive": b}
                         for a, b in boundaries]
    folder = ROOT_RUN / "witness"
    folder.mkdir(parents=True, exist_ok=True)
    shard_file = write_once(folder / "shards.json", {
        "schedule": origin["historical_schedule"], "engine_commit": origin["engine_commit"],
        "market_snapshot": origin["market_snapshot"], "first_time": origin["start"],
        "stop_exclusive": origin["end"], "files": entries, "technical_witness": True,
        "orders_enabled": False,
    })
    return register(source, shard_file, folder / "registration", is_witness=True)


def launch(registration, folder, cost):
    runner, config = registration
    plan = read(checked(config))
    output = folder / "account"
    log, completion = folder / "native.log", folder / "completion.json"
    if not completion.exists():
        with LOCK.open("a") as handle:
            flock(handle, LOCK_EX)
            if log.exists() or output.exists():
                raise FileExistsError("A prior incomplete native attempt must be retained")
            bootstrap = "import runpy,sys; script=sys.argv.pop(1); runpy.run_path(script, run_name='__main__')"
            command = [sys.executable, "-B", "-c", bootstrap, str(checked(runner)), "--plan", str(checked(config)),
                       "--candidate", plan["candidates"][0]["name"], "--cost-bps", str(cost), "--output", str(output)]
            with log.open("x") as stream:
                result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
            save(completion, {"returncode": result.returncode, "output": str(output)})
            result.check_returncode()
    assert read(completion)["returncode"] == 0
    checked(plan["independent_audit_source"])
    reader = IndependentShardRead(plan["data_receipts"])
    summary, _, _ = audit.account(plan, output, cost, candle_reader=reader)
    assert summary["five_minute_nav_reconciliation"]["passed"]
    native_binding = read(output / "sharded_reads_audit.json")
    assert native_binding["counts"]["observer_5m-mark_series"] > 0
    assert native_binding["counts"]["uncached_native_main_pair_loads"] > 0
    summary["independent_candle_reads"] = dict(reader.calls)
    main = summary["periods"]["continuous"]
    trade_input = {"mark_points": main["marks"], "receipts": summary["sources"],
                   "natural_closed_trades": summary["profits"]["natural_closes"]}
    summary["natural_trade_size"] = describe(load_account(trade_input).values())
    save(folder / "account_reconciled.json", summary)
    remove_completed_cache(output, summary)
    return output


def prove_witness():
    location = ROOT_RUN / "witness"
    registration = make_witness()
    output = launch(registration, location, 15)
    anchor = OUT / "accounts/fixed_boost_12_15bp"
    assert read(anchor.parent / "fixed_boost_12_15bp.json")["returncode"] == 0
    for name in ("actual_historical_pairlists.json", "initial_entry_checks.json", "fill_cash_audit.json"):
        assert read(output / name) == read(anchor / name)
    old, = (anchor / "native_results").glob("*.zip")
    fresh, = (output / "native_results").glob("*.zip")
    raw = stats_from(old)["trades"]
    assert raw == stats_from(fresh)["trades"]
    a, b = (pd.read_parquet(path / "five_minute_equity.parquet") for path in (anchor, output))
    pd.testing.assert_frame_equal(a, b, check_exact=True)
    middle = int(pd.Timestamp(WITNESS_BOUNDARY).timestamp() * 1000)
    crossing = [trade["pair"] for trade in raw if trade["open_timestamp"] < middle < trade["close_timestamp"]]
    assert crossing and len(a) == 4608
    save(location / "matched.json", {"exact_fills": True, "exact_full_marked_wallet": True,
                                     "marks": len(a), "instrument_crossing_internal_input_boundary": crossing,
                                     "original": receipt(old), "replay": receipt(fresh), "orders_enabled": False})


def report_continuous(inputs_root=INPUTS, run_root=ROOT_RUN,
                      progress=HERE / "continuous_native_wallet_progress.json"):
    inputs = receipt(inputs_root / "shards.json")
    periods = read(checked(inputs))
    output = {}
    for cost in COSTS:
        rows = {}
        for slots in SLOTS:
            folder = run_root / f"full_{slots}_{cost}bp"
            proof = folder / "account_reconciled.json"
            if proof.exists():
                row = read(proof)
                assert row["periods"]["continuous"]["marks"] == periods["five_minute_marks_expected"]
                assert row["pool"]["decision_days_checked"] == periods["daily_decisions"]
                rows[str(slots)] = {"evidence": receipt(proof), "periods": row["periods"],
                                    "large_trades": row["natural_trade_size"]}
        output[str(cost)] = rows
    ready = all(len(rows) == len(SLOTS) for rows in output.values())
    save(progress, {
        "inputs": inputs, "accounts_by_cost": output, "all_comparable_accounts_completed": ready,
        "completed_accounts": sum(len(rows) for rows in output.values()), "orders_enabled": False,
    })


def run_full(witness_already_verified=False):
    if not witness_already_verified:
        prove_witness()
    proof = read(ROOT_RUN / "witness/matched.json")
    assert proof["exact_fills"] and proof["exact_full_marked_wallet"]
    inputs = receipt(INPUTS / "shards.json")
    for cost in COSTS:
        for slots, origin in frozen_roles().items():
            target = ROOT_RUN / f"full_{slots}_{cost}bp"
            registration = register(origin, inputs, target / "registration", is_witness=False)
            launch(registration, target, cost)
            report_continuous()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("witness", "full", "report"))
    options = parser.parse_args()
    if options.mode == "witness":
        prove_witness()
    elif options.mode == "full":
        run_full((ROOT_RUN / "witness/matched.json").exists())
    else:
        report_continuous()


if __name__ == "__main__":
    main()
