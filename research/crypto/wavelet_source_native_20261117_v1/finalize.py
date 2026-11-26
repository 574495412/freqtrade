"""Seal source admission evidence without claiming models or native accounts ran."""

# ruff: noqa: S101
# These are final source-evidence assertions, not trading code.
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if not __debug__:
    raise RuntimeError("Evidence verification requires assertions")


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def main():
    assert not (HERE / "artifact_manifest.json").exists(), "Preserve sealed evidence"
    for path, digest in read(HERE / "protocol.json")["inputs"].items():
        assert sha(path) == digest, path
        for entry in read(path)["files"]:
            assert sha(entry["path"]) == entry["sha256"], entry["path"]
    old = read(HERE / "chronology_audit.json")
    independent = read(HERE / "independent_verification.json")
    current = read(HERE / "upstream_dispatch_audit_v2.json")
    clocks = read(HERE / "upstream_clock_audit.json")
    owners = read(HERE / "upstream_independent_verification_v2.json")
    assert old["status"] == "LEGACY_SOURCE_CHRONOLOGY_REJECTED"
    assert old["full"]["rows_with_future_training_labels_after128warmup"] == 2871
    assert old["appended200rows_change_past_training_assignment_afterwarmup"] == 974
    assert independent["passed"] and independent["chronology_audit_sha256"] == sha(
        HERE / "chronology_audit.json"
    )
    assert (
        current["passed_source_dispatch_probe"]
        and current["previous_default_signature_mismatch_claim_withdrawn"]
    )
    assert owners["passed"] and not owners["default_arity_error_claim_valid"]
    assert clocks["appended200rows_changed_prior_training_model_clock"] == 0
    assert clocks["full"]["scaling_windows_checked"] == 5000
    assert owners["current_cpu_cold_scaler_rolling_row_clock_reference_matches"]
    assert "All checks passed!" in (HERE / "final_lint.log").read_text()
    assert not (ROOT / "user_data/crypto_quant" / HERE.name).exists()
    correction = read(HERE / "dispatch_probe_correction.json")
    for path, digest in correction["original_probe_inputs"].items():
        assert sha(path) == digest, path
    records = read(HERE / "source_receipts.json") + read(HERE / "upstream_followup_receipts.json")
    for record in records:
        raw = Path(record["local_path"]).read_bytes()
        assert sha(record["local_path"]) == record["sha256"]
        assert (
            hashlib.sha1(
                b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False
            ).hexdigest()
            == record["git_blob"]
        )
    result = {
        "status": "SOURCE_REVIEW_ONLY",
        "legacy_source": "REJECTED_HISTORICAL_PREDICTION_TRAINING_CLOCK",
        "current_upstream": "ROW_CLOCK_CONTROL_PASS_PENDING_REAL_FEATURE_MODEL_NATIVE_INTEGRATION",
        "current_default_signature_error_claim": "WITHDRAWN_PROBE_MRO_ERROR",
        "source_files_pinned": len(records),
        "new_native_wallets": 0,
        "new_financial_runs": 0,
        "new_qualified_alpha": 0,
        "financial_gate": None,
        "net_return": None,
        "drawdown": None,
        "higher_return_proven": False,
        "production_eligible": False,
        "orders_enabled": False,
        "goal_complete": False,
    }
    save(HERE / "admission.json", result)
    verification = {
        "passed": True,
        "verified_at": datetime.now(UTC).isoformat(),
        "verification_scope": (
            "Selected source control-flow, row clocks, source hashes "
            "and prior evidence preservation only"
        ),
        "legacy_diagnostic_independently_reconstructed": True,
        "current_default_mro_corrected_and_independently_checked": True,
        "current_cpu_no_stored_scaler_row_clocks_only": True,
        "current_numerical_feature_or_model_validity_certified": False,
        "native_constructor_or_financial_backtest_executed": False,
        "new_native_wallets": 0,
        "new_qualified_alpha": 0,
        "prior_entropy312files_unchanged": True,
        "source_git_blobs_verified": len(records),
        "orders_enabled": False,
        "services_changed": False,
        "goal_complete": False,
    }
    save(HERE / "verification.json", verification)
    files = [
        path
        for path in HERE.rglob("*")
        if path.is_file()
        and path.name != "artifact_manifest.json"
        and "__pycache__" not in path.parts
    ]
    manifest = {
        "sealed_at": datetime.now(UTC).isoformat(),
        "files": [
            {"path": str(path), "bytes": path.stat().st_size, "sha256": sha(path)}
            for path in sorted(files)
        ],
        "excluded": "No external models or account artifacts; none executed or generated",
    }
    save(HERE / "artifact_manifest.json", manifest)
    for record in read(HERE / "artifact_manifest.json")["files"]:
        assert sha(record["path"]) == record["sha256"], record["path"]
    print(
        json.dumps(
            {
                "passed": True,
                "sealed_files": len(files),
                "manifest_sha256": sha(HERE / "artifact_manifest.json"),
                "new_native_wallets": 0,
                "new_financial_runs": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
