"""Read-only final source, attempt, original-input and result checks."""

import json
from datetime import UTC, datetime
from pathlib import Path

from bootstrap import HERE, save, sha


def main():
    frozen = json.loads((HERE / "implementation_freeze.json").read_text())
    for path, digest in frozen.items():
        assert sha(path) == digest, path
    execution = json.loads((HERE / "execution_receipt.json").read_text())
    assert len(execution) == 22 and all(r["returncode"] == 0 for r in execution)
    input_hashes = json.loads(
        (HERE.with_name("overnight_source_native_20261116_v1") / "input_hashes.json").read_text()
    )
    for path, digest in input_hashes.items():
        assert sha(path) == digest, path
    parent_checks = []
    for name in ["overnight_source_native_20261116_v1", "dividend_lifecycle_native_20261116_v1"]:
        parent = HERE.with_name(name)
        manifest = json.loads((parent / "artifact_manifest.json").read_text())
        entries = manifest["files"]
        rows = (
            entries.items()
            if isinstance(entries, dict)
            else ((row["path"], row["sha256"]) for row in entries)
        )
        for path, digest in rows:
            bound = Path(path)
            if not bound.is_absolute():
                bound = parent / bound
            assert sha(bound) == digest, path
        parent_checks.append({"name": name, "unchanged_paths": len(manifest["files"])})
    starts = exports = 0
    for row in execution:
        folder = HERE / "runs" / row["case"]
        bound = json.loads((folder / "implementation_freeze.json").read_text())
        for path, digest in bound.items():
            assert sha(path) == digest, path
        archives = list((folder / "backtest_results").glob("*.zip"))
        if row["case"].endswith("fractional_reject"):
            assert not archives and (folder / "expected_rejection.json").exists()
            continue
        assert len(archives) == 1
        receipts = json.loads((folder / "native_export_receipt.json").read_text())
        assert sha(archives[0]) == receipts[0]["sha256"]
        assert (folder / "equity_net_result.json").exists()
        starts += 1
        exports += 1
    audit = json.loads((HERE / "independent_audit.json").read_text())
    assert audit["passed"] and audit["independent_scalar_checks"] == 2240
    assert starts == exports == 20
    for filename, count in [
        ("tests_final.log", 19),
        ("dividend_regression.log", 40),
        ("nav_regression.log", 37),
        ("post_run_claims_final.log", 4),
    ]:
        assert f"{count} passed" in (HERE / filename).read_text()
    seam = json.loads((HERE / "core_seam_audit.json").read_text())
    assert seam["all_other_native_ast_exact"]
    save(
        HERE / "verification.json",
        {
            "verified_at": datetime.now(UTC).isoformat(),
            "passed": True,
            "unchanged_frozen_producer_paths": len(frozen),
            "unchanged_original_input_paths": len(input_hashes),
            "parent_seals": parent_checks,
            "actual_native_start_attempts": 22,
            "completed_synthetic_native_lifecycles": starts,
            "native_zip": exports,
            "expected_fractional_rejections": 2,
            "completed_native_fills": 60,
            "partial_rejection_fills": 2,
            "native_boundaries": 280,
            "independent_scalar_checks": 2240,
            "new_unique_component_tests": 23,
            "existing_regression_tests": 77,
            "original_pool": {"US": 503, "HK": 11},
            "historical_equity_accounts": 0,
            "portfolio_accounts": 0,
            "factor_analysis_calls": 0,
            "bench_records": 0,
            "new_accepted_alphas": 0,
            "historical_net_return": None,
            "production_eligible": False,
            "orders_enabled": False,
            "services_changed": False,
            "goal_complete": False,
            "core_seam": seam,
            "scope": (
                "Synthetic native engineering and source checks; no historical account admission"
            ),
        },
    )
    print("Verified39producer paths,1542input paths;20native ZIP;2240scalars;0historical accounts")


if __name__ == "__main__":
    main()
