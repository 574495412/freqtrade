"""Preserve the failed attempt; remove only inventoried generated study caches."""

import os
import shutil

import psutil

from research.crypto.nfi_blacklist_rules_20261109.inputs import (
    HERE,
    OUT,
    ROOT,
    read,
    ref,
    save,
    sha,
)


def remove_cache(cache):
    cache = cache.absolute()
    if cache.name != "lossless_cache" or cache.is_symlink():
        raise ValueError("Unexpected cache deletion scope")
    files = list(cache.rglob("*"))
    if any(f.is_symlink() for f in files):
        raise ValueError("Cache contains symlinks")
    inventory = [
        {"path": str(f.relative_to(cache)), "bytes": f.stat().st_size, "sha256": sha(f)}
        for f in files
        if f.is_file()
    ]
    receipt = cache.parent / "generated-cache-cleanup-recovery.json"
    save(
        receipt,
        {
            "cache": str(cache),
            "files": inventory,
            "bytes_removed": sum(f["bytes"] for f in inventory),
            "only_generated_cache": True,
        },
    )
    shutil.rmtree(cache)
    return ref(receipt)


def main():
    old = ROOT / "user_data/crypto_quant/spot_baseline_short_run_20261105_v003"
    own = {os.getpid(), *(p.pid for p in psutil.Process().parents())}
    for process in psutil.process_iter(["pid", "cmdline"]):
        if process.pid not in own and any(old.name in x for x in (process.info["cmdline"] or [])):
            raise ValueError("Historical cache may be in use")
    failed = OUT / "accounts/native_risk_15bps"
    if read(failed / "completion.json")["returncode"] != 73:
        raise ValueError("Expected actual disk guard failure")
    guard = read(failed / "resource_guard.json")
    if guard["free_disk_bytes"] >= 1.5 * 1024**3 or psutil.pid_exists(guard["pid"]):
        raise ValueError("Failed worker not verified stopped")
    attempts = OUT / "technical_attempts/native_risk_15bps_disk_guard"
    if attempts.exists():
        raise FileExistsError("Preserve prior attempt")
    failed.rename(attempts)
    (OUT / "accounts/native_risk_15bps.log").rename(attempts / "native.log")
    receipts = [remove_cache(attempts / "lossless_cache")]
    preserved = {}
    for name in (
        "nfi-x7-spot-cost13",
        "nfi-x7-spot-cost20",
        "nfi-x8-spot-cost13",
        "nfi-x8-spot-cost20",
    ):
        folder = old / name
        if (
            not (folder / "result.json").exists()
            or not read(folder / "fill_cash_audit.json")["passed"]
        ):
            raise ValueError("Historical native account not completed/cash audited")
        before = {
            str(p): sha(p)
            for p in folder.rglob("*")
            if p.is_file() and "lossless_cache" not in p.parts
        }
        receipts.append(remove_cache(folder / "lossless_cache"))
        if any(sha(p) != h for p, h in before.items()):
            raise ValueError("Noncache historical evidence changed")
        preserved.update(before)
    save(
        HERE / "disk-recovery.json",
        {
            "failed_completion": ref(attempts / "completion.json"),
            "resource_guard": ref(attempts / "resource_guard.json"),
            "generated_cache_inventories": receipts,
            "unchanged_historical_noncached_evidence_sha256": preserved,
            "free_bytes_after": shutil.disk_usage(OUT).free,
            "parameters_and_native_sources_changed": False,
            "retry_uses_identical_frozen_plan": ref(HERE / "native_risk-plan.json"),
            "orders_enabled": False,
        },
    )
    print(
        {
            "free_gib": shutil.disk_usage(OUT).free / 1024**3,
            "preserved_failure": str(attempts),
            "same_parameters": True,
        }
    )


if __name__ == "__main__":
    main()
