"""Pure object/resource-receipt tests; no native backtester, wallet or subprocess."""

import copy
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from .admission import sha
from .execute_pair import terminal_copy
from .native_producer import Resources, save
from .technical_contract import (
    observed_storage_coverage,
    resource_failures,
    verify_terminal_resources,
)


def rejected(operation, text):
    try:
        operation()
    except (RuntimeError, ValueError) as error:
        assert text in str(error), str(error)
    else:
        raise AssertionError("Expected rejection: " + text)


def run():
    limits = {
        "max_rss_bytes": 3000,
        "minimum_system_available_bytes": 1500,
        "minimum_free_disk_bytes": 5000,
        "max_seconds_per_variant": 1800,
    }
    good = {
        "worker_pid": 123,
        "rss_bytes": 2000,
        "peak_sampled_rss_bytes": 2500,
        "available_bytes": 2000,
        "minimum_available_bytes": 1600,
        "free_disk_bytes": 6000,
        "minimum_free_disk_bytes": 5500,
        "elapsed_seconds": 1799.0,
    }
    completed = []
    with tempfile.TemporaryDirectory(prefix="nfi-terminal-resource-") as folder:
        root = Path(folder)

        def resource(name, sample, failed=False, alive=False):
            output = root / name
            output.mkdir()
            obj = Resources.__new__(Resources)
            obj.output, obj.limits = output, limits
            obj.stop = SimpleNamespace(set=lambda: None)
            obj.monitor = SimpleNamespace(join=lambda timeout: None, is_alive=lambda: alive)
            obj.sample = lambda: (copy.deepcopy(sample), failed)
            return obj

        passing = resource("pass", good)
        result = passing.finish()
        assert not resource_failures(result, limits)
        process = {"pid": 123, "resources_sha256": sha(passing.output / "resources.json")}
        assert verify_terminal_resources(passing.output, process, limits) == result
        completed.append("successful_final_sample_written_and_controller_sha_identity_bound")

        # Synthetic receipt files exercise the real controller acceptance
        # function without creating a subprocess or a native account/archive.
        placeholder = passing.output / "synthetic_placeholder.txt"
        placeholder.write_text("Synthetic resource-gate object test only\n")
        reference = {"path": str(placeholder), "sha256": sha(placeholder)}
        save(
            passing.output / "native_lifecycle_receipt.json",
            {
                "terminal_exit_code": None,
                "worker_method_completed": True,
                "native_zip": reference,
                "full_semantic_trace": reference,
                "synthetic_receipt_fixture": True,
            },
        )
        terminal = {
            **process,
            "terminal_witness": "Popen.wait",
            "returncode": 0,
            "receipt_sha256": sha(passing.output / "native_lifecycle_receipt.json"),
        }
        assert (
            terminal_copy(passing.output, terminal, {"resource_limits": limits})[
                "terminal_exit_code"
            ]
            == 0
        )
        unbound = {**terminal, "resources_sha256": "changed"}
        rejected(
            lambda: terminal_copy(passing.output, unbound, {"resource_limits": limits}),
            "resources.json",
        )
        completed.append("actual_controller_terminal_copy_requires_bound_resource_receipt")

        final = {**good, "rss_bytes": 3001, "peak_sampled_rss_bytes": 3001}
        failing = resource("final_first_breach", final, failed=True)
        rejected(failing.finish, "Final resource sample rejected")
        saved = json.loads((failing.output / "resources.json").read_text())
        assert (
            saved["final_sample_passed"] is False and "rss_limit" in saved["terminal_gate_failures"]
        )
        assert (failing.output / "terminal_resource_failure.json").is_file()
        completed.append("first_breach_only_at_final_sample_rejects_and_preserves_receipt")

        # Even a false producer success flag cannot bypass the controller's
        # independent bounds validation over the actual resource receipt.
        for field, bad in [
            ("rss_bytes", 3001),
            ("peak_sampled_rss_bytes", 3001),
            ("available_bytes", 1499),
            ("minimum_available_bytes", 1499),
            ("free_disk_bytes", 4999),
            ("minimum_free_disk_bytes", 4999),
            ("elapsed_seconds", 1800.001),
        ]:
            changed = {**result, field: bad, "final_sample_passed": True}
            path = root / field
            path.mkdir()
            save(path / "resources.json", changed)
            bound = {"pid": 123, "resources_sha256": sha(path / "resources.json")}
            rejected(
                lambda path=path, bound=bound: verify_terminal_resources(path, bound, limits),
                "resource gate failed",
            )
        completed.append("controller_recomputes_all_seven_current_or_historical_bounds")
        for altered in [
            {"pid": 123, "resources_sha256": "wrong"},
            {"pid": 124, "resources_sha256": process["resources_sha256"]},
            {"pid": 123},
        ]:
            rejected(
                lambda altered=altered: verify_terminal_resources(passing.output, altered, limits),
                "resource",
            )
        for key in good:
            bad = {**result}
            bad.pop(key)
            assert resource_failures(bad, limits)
        assert resource_failures({**result, "rss_bytes": float("nan")}, limits)
        completed.append("missing_digest_identity_numeric_fields_and_nan_reject")
        alive = resource("monitor_alive", good, alive=True)
        rejected(alive.finish, "monitor_not_stopped")
        completed.append("monitor_thread_not_terminal_rejects")

        ev = {"native_callback_dp_reads": 4, "native_callback_cross_block_reads": 1}
        baseline = {"cache_path_evidence": dict(ev)}
        candidate = {
            "cache_path_evidence": dict(ev),
            "measurements": {"block_stats": [{"evictions": 2, "maximum_cached_blocks": 32}]},
        }
        coverage = observed_storage_coverage(baseline, candidate)
        assert coverage["observed_native_callback_block_crossing_and_eviction"]
        assert not coverage["complete_cache_lifecycle_certified"]
        for mutation in ("no_cross", "no_eviction", "no_capacity", "unequal_paths"):
            c = copy.deepcopy(candidate)
            if mutation == "no_cross":
                c["cache_path_evidence"]["native_callback_cross_block_reads"] = 0
            elif mutation == "no_eviction":
                c["measurements"]["block_stats"][0]["evictions"] = 0
            elif mutation == "no_capacity":
                c["measurements"]["block_stats"][0]["maximum_cached_blocks"] = 31
            else:
                c["cache_path_evidence"]["native_callback_dp_reads"] = 5
            rejected(lambda c=c: observed_storage_coverage(baseline, c), "")
        completed.append("cross_block_eviction_capacity_path_gate_rejects_missing_real_evidence")
    return {
        "passed": True,
        "checks": completed,
        "synthetic_resource_and_receipt_objects_only": True,
        "native_accounts_started": 0,
        "subprocesses_started": 0,
        "financial_lock_acquired": False,
        "actual_native_path_coverage": "UNKNOWN",
        "execution_replacement_admitted": False,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
