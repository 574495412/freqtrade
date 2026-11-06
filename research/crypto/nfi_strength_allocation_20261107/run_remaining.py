"""Run registered accounts serially and reclaim only their disposable caches."""

import shutil
import subprocess
import sys

from research.crypto.nfi_strength_allocation_20261107.audit import account, complete
from research.crypto.nfi_strength_allocation_20261107.register import OUT, PARENT, PLAN
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha


def main():
    runner = PARENT / "run_wide_comparison.py"
    for strategy, cost in (
        ("StrengthAllocation", 15),
        ("FlatTopBoost", 15),
        ("StrengthAllocation", 8),
        ("FlatTopBoost", 8),
    ):
        name = f"{strategy}_{cost}bps"
        path = OUT / name
        result = path / "result.json"
        if not result.exists():
            if path.exists():
                raise FileExistsError(f"Started account has no terminal result: {path}")
            with (OUT / f"{name}.log").open("x") as log:
                subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        str(runner),
                        "--plan",
                        str(PLAN),
                        "--candidate",
                        strategy,
                        "--cost-bps",
                        str(cost),
                        "--output",
                        str(path),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
        audit = account(strategy, cost)
        cache = path / "lossless_cache"
        if cache.is_dir() and not cache.is_symlink():
            size = sum(p.stat().st_size for p in cache.rglob("*") if p.is_file())
            shutil.rmtree(cache)
            save(
                path / "cache-cleanup.json",
                {
                    "removed_bytes": size,
                    "result_sha256": sha(result),
                    "audit": audit["archive_sha256"],
                },
            )
    complete()


if __name__ == "__main__":
    main()
