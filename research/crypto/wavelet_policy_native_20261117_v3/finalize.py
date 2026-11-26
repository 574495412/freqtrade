"""Seal source-policy native admission without profitability promotion."""

# ruff: noqa: S101
import json
from datetime import UTC, datetime
from pathlib import Path

from research.crypto.wavelet_policy_native_20261117_v3.verify_policy import (
    HERE,
    OUTPUT,
    ROOT,
    read,
    sha,
)


def save(name, obj):
    (HERE / name).write_text(json.dumps(obj, indent=2, allow_nan=False) + "\n")


def main():
    assert not (HERE / "artifact_manifest.json").exists(), "Preserve sealed evidence"
    verified = read(HERE / "independent_verification.json")
    assert verified["passed"] and verified["native_indicator_and_signal_rows_checked"] == 11600
    assert verified["custom_exit_grid_cases_checked"] == 96
    native = read(OUTPUT / "attempt2/admission.json")
    assert native["passed"] and not native["financial_start_called"]
    official_path = Path("/Users/chrise/GitHub/CZ-Agent/vendor/FREQTRADE_SOURCE.json")
    assert sha(official_path) == "c0d66d07ecd46b6506af8f5408c61185adb8fc0f63d1e7652f5bf4a104a3bc64"
    official = read(official_path)
    for relative, digest in official["files"].items():
        assert sha(official_path.parent / "freqtrade" / relative) == digest
    assert "All checks passed!" in (HERE / "final_lint.log").read_text()
    assert not list(OUTPUT.rglob("*.zip"))
    for path, digest in read(HERE / "probe_label_correction.json")["preserved_files"].items():
        assert sha(path) == digest
    save(
        "admission.json",
        {
            "status": "SOURCE_POLICY_NATIVE_INTERFACE_ADMITTED_FINANCIAL_RUN_PENDING",
            "real_native_source_indicator_and_signal_cases": 6,
            "native_indicator_and_signal_rows": 11600,
            "active_native_parameters_independently_checked": verified[
                "native_parameter_values_checked"
            ],
            "source_policy_and_numerics_not_retuned": True,
            "native_stop_sizing_entry_and_exit_callback_contracts_passed": True,
            "full_original_top_level_import_startup_proven": False,
            "financial_start_called": False,
            "new_native_financial_wallets": 0,
            "new_qualified_alpha": 0,
            "predictive_power_proven": False,
            "net_return": None,
            "drawdown": None,
            "higher_return_proven": False,
            "orders_enabled": False,
            "production_eligible": False,
            "goal_complete": False,
        },
    )
    save(
        "verification.json",
        {
            "passed": True,
            "verified_at": datetime.now(UTC).isoformat(),
            "independent_verification_sha256": sha(HERE / "independent_verification.json"),
            "official_engine_commit": official["commit"],
            "official_source_files_verified": len(official["files"]),
            "scope": (
                "Fixed CPU source policy and native callback contracts, not financial performance"
            ),
            "previous_goal_turn": "progress",
            "goal_complete": False,
        },
    )
    files = [
        p
        for root in [HERE, OUTPUT]
        for p in root.rglob("*")
        if p.is_file()
        and p.name != "artifact_manifest.json"
        and "__pycache__" not in p.parts
        and ".pytest_cache" not in p.parts
    ]
    save(
        "artifact_manifest.json",
        {
            "sealed_at": datetime.now(UTC).isoformat(),
            "files": [
                {"path": str(p), "bytes": p.stat().st_size, "sha256": sha(p)} for p in sorted(files)
            ],
            "excluded": (
                "Bytecode/test caches; previous studies and dependencies verified by reference"
            ),
            "native_engine_unchanged": True,
            "goal_complete": False,
        },
    )
    for row in read(HERE / "artifact_manifest.json")["files"]:
        assert sha(row["path"]) == row["sha256"]
    print(
        json.dumps(
            {
                "passed": True,
                "sealed_files": len(files),
                "manifest_sha256": sha(HERE / "artifact_manifest.json"),
                "new_financial_wallets": 0,
                "goal_complete": False,
                "root": str(ROOT),
            }
        )
    )


if __name__ == "__main__":
    main()
