"""Repeat the unchanged 8bp native account after the first output disk failure."""

from datetime import UTC, datetime
from fcntl import LOCK_EX, flock
import hashlib
from pathlib import Path
import pickle
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import LOCK, ROOT, checked, read, receipt, save
from research.crypto.nfi80_extended_windows_20261126_v1.accounts import remove_completed_cache
from research.crypto.nfi80_online_baseline_20261125_v1.audit import main as audit_comparison

from . import bounded_row_storage, bounded_storage


HERE = Path(__file__).resolve().parent


def technology_check(folder):
    """Exercise exact memory, spill, mutation and consumption on native output rows."""
    frame = pd.read_parquet(folder / "five_minute_equity.parquet").iloc[:150]
    expected = frame.assign(equity=frame.equity + .25)
    initial_limit = bounded_storage.MEMORY_LIMIT
    try:
        bounded_storage.MEMORY_LIMIT = 20_000
        with TemporaryDirectory() as temp:
            path = Path(temp).resolve()
            memory = bounded_storage.DiskMapping(path / "memory", 0)
            original_blob = hashlib.sha256(pickle.dumps(frame, protocol=5)).hexdigest()
            memory["frame"] = frame
            assert memory.compressed and memory.memory_bytes > 0
            assert original_blob == memory.digests["frame"]
            pd.testing.assert_frame_equal(memory["frame"], frame, check_exact=True)
            assert bounded_row_storage.release_consumed(memory, "frame", path) == 0
            assert not memory and memory.memory_bytes == 0
            bounded_storage.MEMORY_LIMIT = 0
            spill = bounded_storage.DiskMapping(path / "spill", 1)
            disk_blob = hashlib.sha256(pickle.dumps(frame, protocol=5)).hexdigest()
            spill["frame"] = frame
            assert not spill.compressed and spill.paths["frame"].is_file()
            assert disk_blob == spill.digests["frame"]
            spill["frame"]["equity"] += .25
            spill["new"] = frame
            pd.testing.assert_frame_equal(spill["frame"], expected, check_exact=True)
            assert spill.digests["frame"] != disk_blob
            assert bounded_row_storage.release_consumed(spill, "frame", path) > 0
            assert len(spill) == 1 and len(list(spill.root.glob("*.pkl"))) == 1
    finally:
        bounded_storage.MEMORY_LIMIT = initial_limit
    return {"all_checks_passed": True, "memory_byte_limit": initial_limit, "compression_level": 6,
            "roundtrip_on_memory_and_disk": True, "native_in_place_mutation_replayed": True}


def main():
    parent = HERE / "plan.json"
    failure = read(HERE / "failed_8bp_evidence.json")
    failed = Path(failure["saved_failure"])
    for source in failure["artifacts"]:
        checked(source)
    assert read(HERE / "comparison_15bp.json")["advance"]
    proof = technology_check(failed)
    files = {
        "storage_source": receipt(HERE / "bounded_storage.py"),
        "row_storage": receipt(HERE / "bounded_row_storage.py"),
    }
    prior = read(parent)
    proof.update({"original_plan": receipt(parent), "original_storage": prior["storage_source"],
                  "original_rows": prior["row_storage"], "retry_storage": files})
    for source in (proof["original_storage"], proof["original_rows"]):
        checked(source)
    validation = HERE / "bounded_storage_validation.json"
    save(validation, proof)
    revised = {**prior, **files, "storage_validation": receipt(validation)}
    plan_path = HERE / "plan_8bp_storage_retry.json"
    output = ROOT / "user_data/crypto_quant" / HERE.name / "cost_8bp"
    if output.exists() or plan_path.exists():
        raise FileExistsError("An earlier retry already exists; preserve it")
    save(plan_path, revised)
    candidate, = prior["candidates"]
    with LOCK.open("a") as lock:
        flock(lock, LOCK_EX)
        if shutil.disk_usage(ROOT).free < 7 * 1024**3:
            raise OSError("Insufficient space for the bounded native replay")
        run = subprocess.run([
            sys.executable, "-B", "-m", "research.crypto.nfi80_historical_universe_20261109.v003.native_runner",
            "--plan", str(plan_path), "--candidate", candidate["name"], "--cost-bps", "8",
            "--output", str(output),
        ], cwd=ROOT)
        save(HERE / "execution_8bp.json", {"returncode": run.returncode, "output": str(output),
             "finished_at_utc": datetime.now(UTC).isoformat(), "orders_enabled": False})
        run.check_returncode()
    first_archive, = (failed / "native_results").glob("*.zip")
    new_archive, = (output / "native_results").glob("*.zip")
    trades_equal = stats_from(first_archive)["trades"] == stats_from(new_archive)["trades"]
    assert trades_equal
    pd.testing.assert_frame_equal(pd.read_parquet(failed / "five_minute_equity.parquet"),
                                  pd.read_parquet(output / "five_minute_equity.parquet"), check_exact=True)
    save(HERE / "bounded_storage_parity.json", {"exact_native_trades": trades_equal,
         "exact_five_minute_equity": True, "first_native_archive": receipt(first_archive),
         "retry_native_archive": receipt(new_archive), "first_report_failed_and_preserved": True})
    audit_comparison(8, False, HERE, plan_path=plan_path)
    outcome = read(HERE / "comparison_8bp.json")
    remove_completed_cache(output, outcome["candidate"])


if __name__ == "__main__":
    main()
