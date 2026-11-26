"""Freeze and run the native storage-only extension on verified account plans."""

import argparse
from fcntl import LOCK_EX, flock
from pathlib import Path
import subprocess
import sys

import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import LOCK, ROOT, checked, read, receipt, save
from research.crypto.nfi80_online_baseline_20261125_v1.audit import account, main as compare_discovery

from .accounts import COSTS, remove_completed_cache, write_once
from .annual_archives import DEST
from .inputs import HERE, OUT


V003 = HERE.parent / "nfi80_historical_universe_20261109/v003/native_runner.py"
CACHE_VALIDATOR = HERE.parent / "nfi40_frame_cache_native_20261117_v1/frame_storage_v3.py"
RESEARCH = OUT / "bounded_native"
BLOCK_RESEARCH = OUT / "bounded_native_blocks"
CAP_STUDY = HERE.parent / "nfi80_online_rebuy_cap_20261125_v1"


def render_runner(plan, path):
    original = checked({"path": V003, "sha256": plan["runner_sha256"]})
    code = original.read_text()

    def replace(before, after):
        nonlocal code
        if code.count(before) != 1:
            raise ValueError(f"Unexpected native source stage: {before}")
        code = code.replace(before, after, 1)

    replace(
        "return row_module.LazyTrimMapping(\n            frames, lambda value: original_trim(value, timerange, startup_candles)\n        )",
        "return storage.trim_dates(frames, original_trim, timerange, startup_candles)",
    )
    replace("provider = account.dataprovider\n        initial_entry_checks",
            "provider = account.dataprovider\n        replay_storage = storage.ReplayStorage(account)\n        initial_entry_checks")
    begin = code.index("                result = storage.DiskMapping(cache / \"indicators\", 1)")
    end = code.index("\n\n            strategy.advise_all_indicators = advise", begin)
    replace(code[begin:end], "                return replay_storage.advise(frames, operation)")
    replace("storage.DiskMapping(\n                cache / f\"analyzed_{serial}\", 14\n            )",
            "replay_storage.install(cache / f\"analyzed_{serial}\")")
    replace("account.start()\n        save(output / \"initial_entry_checks.json\"",
            "account.start()\n        save(output / \"bounded_storage_audit.json\", replay_storage.audit())\n        save(output / \"initial_entry_checks.json\"")
    if path.exists():
        assert path.read_text() == code
    else:
        path.write_text(code)
    compile(code, str(path), "exec")
    return receipt(path)


def register(source, folder, backend):
    folder.mkdir(parents=True, exist_ok=True)
    parent = read(source)
    runner = render_runner(parent, folder / "runner.py")
    storage_path = "low_disk_block_storage.py" if backend == "blocks" else "low_disk_storage.py"
    storage = receipt(HERE / storage_path)
    rows = receipt(HERE / "low_disk_rows.py")
    inputs = {
        "parent": receipt(source), "old_runner": receipt(V003), "runner": runner,
        "storage": storage, "rows": rows, "existing_rows": receipt(V003.with_name("row_storage.py")),
        "existing_frame_fingerprint": receipt(CACHE_VALIDATOR),
        "intact_native_policy": "Matching, indicators, strategy, data, clocks and wallet unchanged.",
    }
    if backend == "blocks":
        inputs["common_storage_lifecycle"] = receipt(HERE / "low_disk_storage.py")
    anchor = write_once(folder / "storage_registration.json", inputs)
    revised = {**parent, "runner_sha256": runner["sha256"], "storage_source": storage,
               "row_storage": rows, "storage_registration": anchor}
    path = write_once(folder / "plan.json", revised)
    return runner, path


def execute(source, folder, cost, backend="frames", *, check_sizing=None):
    runner, plan_path = register(source, folder / "registration", backend)
    plan = read(checked(plan_path))
    evidence = read(checked(plan["storage_registration"]))
    for row in evidence.values():
        if isinstance(row, dict):
            checked(row)
    candidate, = plan["candidates"]
    output = folder / "account"
    log = folder / "native.log"
    completion = folder / "completion.json"
    if not completion.exists():
        with LOCK.open("a") as handle:
            flock(handle, LOCK_EX)
            if output.exists() or log.exists():
                raise FileExistsError("Retain the unfinished storage or native attempt")
            launcher = "import runpy,sys; script=sys.argv.pop(1); runpy.run_path(script, run_name='__main__')"
            command = [
                sys.executable, "-B", "-c", launcher, runner["path"], "--plan", plan_path["path"],
                "--candidate", candidate["name"], "--cost-bps", str(cost), "--output", str(output),
            ]
            with log.open("x") as stream:
                result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
            save(completion, {"returncode": result.returncode, "output": str(output)})
            result.check_returncode()
    assert read(completion)["returncode"] == 0
    if check_sizing is None:
        check_sizing = candidate["name"] != "original"
    summary, _, _ = account(plan, output, cost, check_sizing=check_sizing)
    assert summary["five_minute_nav_reconciliation"]["passed"]
    metrics = read(output / "bounded_storage_audit.json")
    assert metrics["provider_assignments"] == len(plan["pairs"])
    assert metrics["exact_rebuilds"] > 0 and metrics["peak_analyzed_cache_bytes"] <= 2 * 1024 ** 3
    save(folder / "account_reconciled.json", summary)
    remove_completed_cache(output, summary)
    print(candidate["name"], cost, summary["periods"]["continuous"], metrics, flush=True)
    return output


def parity(cost, research, backend):
    reference = OUT / "accounts" / f"fixed_boost_12_{cost}bp"
    completion = read(reference.parent / f"fixed_boost_12_{cost}bp.json")
    assert completion["returncode"] == 0
    folder = research / f"recent_parity_{cost}bp"
    result = execute(OUT / "registrations/fixed_boost_12-plan.json", folder, cost, backend)
    fields = ("fill_cash_audit.json", "initial_entry_checks.json", "actual_historical_pairlists.json")
    assert all(read(reference / field) == read(result / field) for field in fields)
    old, = (reference / "native_results").glob("*.zip")
    new, = (result / "native_results").glob("*.zip")
    assert stats_from(old)["trades"] == stats_from(new)["trades"]
    pd.testing.assert_frame_equal(
        pd.read_parquet(reference / "five_minute_equity.parquet"),
        pd.read_parquet(result / "five_minute_equity.parquet"), check_exact=True,
    )
    save(folder / "parity.json", {
        "exact_trades": True, "exact_marked_wallet": True, "exact_native_replays": fields,
        "original_trades": receipt(old), "tested_trades": receipt(new),
        "technology_scope": "Single matched 16-day account, plus full-frame digest on every runtime rebuild.",
        "orders_enabled": False,
    })
    print("exact parity", cost, flush=True)


def retry_discovery_cap():
    assert read(BLOCK_RESEARCH / "recent_parity_8bp/parity.json")["exact_marked_wallet"]
    loss = read(CAP_STUDY / "failed_8bp_evidence.json")
    known = {checked(row).suffix: row for row in loss["artifacts"]}
    folder = BLOCK_RESEARCH / "cap_discovery_8bp"
    current = execute(CAP_STUDY / "plan.json", folder, 8, "blocks")
    new, = (current / "native_results").glob("*.zip")
    assert stats_from(checked(known[".zip"]))["trades"] == stats_from(new)["trades"]
    pd.testing.assert_frame_equal(
        pd.read_parquet(checked(known[".parquet"])),
        pd.read_parquet(current / "five_minute_equity.parquet"), check_exact=True,
    )
    save(folder / "parity.json", {
        "exact_trades": True, "exact_marked_wallet": True,
        "first_attempt_financial_result_admitted": False,
        "first_attempt_trades": known[".zip"], "new_native_trades": receipt(new),
    })
    compare_discovery(8, False, CAP_STUDY, plan_path=folder / "registration/plan.json",
                      candidate_folder=current, completion_path=folder / "completion.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("parity", "annual", "cap-discovery"))
    parser.add_argument("--cost", type=int, choices=COSTS, default=15)
    parser.add_argument("--year", type=int, choices=(2024, 2025), default=2025)
    parser.add_argument("--role", choices=("original", "fixed_boost_12", "boost_14"), default="original")
    parser.add_argument("--backend", choices=("frames", "blocks"), default="frames")
    args = parser.parse_args()
    research = BLOCK_RESEARCH if args.backend == "blocks" else RESEARCH
    if args.mode == "cap-discovery":
        retry_discovery_cap()
    elif args.mode == "parity":
        parity(args.cost, research, args.backend)
    else:
        recent = research / f"recent_parity_{args.cost}bp/parity.json"
        assert read(recent)["exact_marked_wallet"]
        root = DEST if args.year == 2025 else OUT / f"annual_{args.year}"
        prefix = "annual" if args.year == 2025 else f"annual_{args.year}"
        source = root / "registrations" / f"{args.role}-plan.json"
        execute(source, research / f"{prefix}_{args.role}_{args.cost}bp", args.cost, args.backend)


if __name__ == "__main__":
    main()
