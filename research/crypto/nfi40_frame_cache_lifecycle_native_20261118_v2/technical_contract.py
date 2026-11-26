"""Resource/observed-storage gates only; never constructs or runs an account."""

import math

from .admission import read, sha


def resource_failures(receipt, limits):
    required = (
        "rss_bytes",
        "peak_sampled_rss_bytes",
        "available_bytes",
        "minimum_available_bytes",
        "free_disk_bytes",
        "minimum_free_disk_bytes",
        "elapsed_seconds",
        "worker_pid",
    )
    if any(
        not isinstance(receipt.get(k), (int, float))
        or isinstance(receipt.get(k), bool)
        or not math.isfinite(receipt[k])
        or receipt[k] < 0
        for k in required
    ):
        return ["missing_or_invalid_resource_numbers"]
    checks = {
        "final_sample_failed": receipt.get("final_sample_passed") is not True,
        "monitor_not_stopped": receipt.get("monitor_stopped") is not True,
        "rss_limit": max(receipt["rss_bytes"], receipt["peak_sampled_rss_bytes"])
        > limits["max_rss_bytes"],
        "available_floor": min(receipt["available_bytes"], receipt["minimum_available_bytes"])
        < limits["minimum_system_available_bytes"],
        "disk_floor": min(receipt["free_disk_bytes"], receipt["minimum_free_disk_bytes"])
        < limits["minimum_free_disk_bytes"],
        "elapsed_limit": receipt["elapsed_seconds"] > limits["max_seconds_per_variant"],
    }
    return [name for name, failed in checks.items() if failed]


def verify_terminal_resources(output, process, limits):
    path = output / "resources.json"
    if not path.is_file() or process.get("resources_sha256") != sha(path):
        raise ValueError("Terminal process receipt does not bind resources.json")
    receipt = read(path)
    if receipt.get("schema") != "native-worker-terminal-resources.v2":
        raise ValueError("Wrong terminal resource receipt schema")
    if receipt.get("worker_pid") != process.get("pid"):
        raise ValueError("Terminal resource receipt worker identity differs")
    if failures := resource_failures(receipt, limits):
        raise ValueError("Terminal resource gate failed: " + ",".join(failures))
    return receipt


def observed_storage_coverage(baseline, candidate):
    """Require actual callback slices across a block and actual 32-block eviction."""
    evidence = []
    for receipt in (baseline, candidate):
        row = receipt.get("cache_path_evidence", {})
        if (
            row.get("native_callback_dp_reads", 0) <= 0
            or row.get("native_callback_cross_block_reads", 0) <= 0
        ):
            raise ValueError("Observed native callback cross-block coverage missing")
        evidence.append(row)
    if evidence[0] != evidence[1]:
        raise ValueError("Native cache slice-path coverage differs")
    stats = candidate.get("measurements", {}).get("block_stats", [])
    if not stats or sum(s.get("evictions", 0) for s in stats) <= 0:
        raise ValueError("Actual block-store LRU eviction was not observed")
    if max(s.get("maximum_cached_blocks", 0) for s in stats) != 32:
        raise ValueError("Actual block-store 32-block capacity was not observed")
    return {
        "observed_native_callback_block_crossing_and_eviction": True,
        "evidence": evidence[1],
        "actual_block_evictions": sum(s["evictions"] for s in stats),
        "complete_cache_lifecycle_certified": False,
        "remaining_scope": "Fixed short observed paths only; 8192-boundary synthetic native fixture and full-window replay remain unexecuted",
        "execution_replacement_admitted": False,
    }
