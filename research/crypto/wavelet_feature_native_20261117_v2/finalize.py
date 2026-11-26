"""Seal real numerical/native-feature evidence without financial promotion."""

# ruff: noqa: S101
# Assertions verify immutable evidence; this script executes no predictor/account.
import ast
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OFFICIAL = Path("/Users/chrise/GitHub/CZ-Agent/vendor/FREQTRADE_SOURCE.json")
OFFICIAL_SHA = "c0d66d07ecd46b6506af8f5408c61185adb8fc0f63d1e7652f5bf4a104a3bc64"
WAVELET_SHA = "64dbe218c1e7cad16e4a236b74e25a39bed64adc02fb7f13412032f9d57e7191"
ENTROPY_SHA = "df4fa110d046bb3f809dad3a6b5b5ec1204ed4a3f787448627bb735355e66b18"
PARENTS = {
    "wavelet_source_native_20261117_v1": WAVELET_SHA,
    "permutation_entropy_source_native_20261117_v1": ENTROPY_SHA,
}
if not __debug__:
    raise RuntimeError("Evidence verification requires assertions")


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save(name, obj):
    (HERE / name).write_text(json.dumps(obj, indent=2, allow_nan=False) + "\n")


def verify_sources():
    records = []
    for name in [
        "source_receipts.json",
        "supplemental_source_receipts.json",
        "feature_dependency_followup.json",
        "runtime_owner_followup.json",
    ]:
        records.extend(read(HERE / name))
    for row in records:
        raw = Path(row["local_path"]).read_bytes()
        assert sha(row["local_path"]) == row["sha256"]
        blob = hashlib.sha1(
            b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False
        ).hexdigest()
        assert blob == row["git_blob"]
        assert row["commit"] == "9bb7dab5df53203e69f60336749551a40767af0b"
    return len(records)


def verify_parents():
    parents = []
    for name, digest in PARENTS.items():
        manifest = HERE.with_name(name) / "artifact_manifest.json"
        assert sha(manifest) == digest
        rows = read(manifest)["files"]
        for row in rows:
            assert sha(row["path"]) == row["sha256"], row["path"]
        parents.append({"path": str(manifest), "sha256": digest, "files_verified": len(rows)})
    return parents


def verify_dependencies():
    references = []
    local = read(HERE / "dependency_manifest.json")
    reused = Path(local["reused_package_manifest"])
    receipt = read(HERE / "dependency_import_receipt.json")
    assert sha(reused) == receipt["reused_dependency_manifest_sha256"]
    for manifest, folder in [
        (HERE / "dependency_manifest.json", HERE / "dependencies"),
        (reused, reused.parent / "dependencies"),
    ]:
        rows = read(manifest)["files"]
        for relative, digest in rows.items():
            assert sha(folder / relative) == digest, relative
        references.append(
            {"path": str(manifest), "sha256": sha(manifest), "files_verified": len(rows)}
        )
    for row in read(HERE / "wheel_receipts.json")["records"]:
        assert sha(row["path"]) == row["sha256"]
        assert Path(row["path"]).stat().st_size == row["bytes"]
    return references


def precision_method_receipt():
    source = HERE / "sources/Framework/BaseStrategy.py"
    cls = next(
        c
        for c in ast.parse(source.read_text()).body
        if isinstance(c, ast.ClassDef) and c.name == "BaseStrategy"
    )
    method = next(
        m
        for m in cls.body
        if isinstance(m, ast.FunctionDef) and m.name == "check_precision_columns"
    )
    return {
        "path": str(source),
        "sha256": sha(source),
        "class": cls.name,
        "method": method.name,
        "line": method.lineno,
        "method_defined": True,
        "full_original_framework_startup_tested": False,
    }


def main():
    assert not (HERE / "artifact_manifest.json").exists(), "Preserve sealed evidence"
    parents = verify_parents()
    dependencies = verify_dependencies()
    source_count = verify_sources()
    for name in ["implementation_freeze.json", "native_interface_freeze.json"]:
        for path, digest in read(HERE / name)["files"].items():
            assert sha(path) == digest, path
    assert sha(OFFICIAL) == OFFICIAL_SHA
    official = read(OFFICIAL)
    for relative, digest in official["files"].items():
        assert sha(OFFICIAL.parent / "freqtrade" / relative) == digest, relative
    independent = read(HERE / "independent_verification.json")
    numerical = read(HERE / "numerical_admission.json")
    native = read(HERE / "native_feature_admission.json")
    full = read(HERE / "native_full_numerical_parity.json")
    assert independent["passed"] and numerical["passed"] and native["passed"] and full["passed"]
    assert independent["source_real_fit_calls_checked"] == 12
    assert independent["native_feature_rows"] == 4000
    assert independent["native_full_retraining_case_rows"] == 6000
    assert full["training_windows"] == 2 and full["source_reference_exactly_equal"]
    assert native["entry_and_exit_evaluation_explicitly_refused"]
    assert not native["financial_start_called"] and native["native_trades"] == 0
    assert not native["native_results_zip_generated"]
    assert sha(full["output"]["path"]) == full["output"]["sha256"]
    for row in native["actual_feature_rows"]:
        assert sha(row["feature_path"]) == row["sha256"]
    assert "3 passed" in (HERE / "native_feature_tests.log").read_text()
    assert "All checks passed!" in (HERE / "final_lint.log").read_text()
    assert (HERE / "attempt1/numerical_probe.py").exists()
    assert (HERE / "verify_results_v001.py.txt").exists()
    storage = read(HERE / "storage_dedup_receipt.json")
    for row in storage["replaced_paths"]:
        assert sha(row["source"]) == sha(row["target"]) == row["sha256"]
        a, b = Path(row["source"]).stat(), Path(row["target"]).stat()
        assert (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
    save(
        "remaining_integration_contract.json",
        {
            "status": "FULL_STRATEGY_NATIVE_FINANCIAL_INTEGRATION_PENDING",
            "source_hook_static_verification": precision_method_receipt(),
            "missing_or_unverified": [
                "Complete author entry/exit guards, stop/risk/sizing and parameter-file precedence",
                "Full original framework startup and complete indicator/guard integration",
                "Frozen financial universe/capital/cost/execution/account contract",
                "Complete official-engine financial accounts and independent cash/NAV audit",
                "Predictive-power, grouped-return and independent holdout validation",
            ],
            "local_feature_module_admitted": True,
            "current_adapter_entry_exit_refuse_execution": True,
            "missing_precision_method_claim_valid": False,
            "new_financial_wallets": 0,
            "new_qualified_alpha": 0,
        },
    )
    save(
        "admission.json",
        {
            "status": "NUMERICAL_AND_NATIVE_FEATURE_ADMITTED_FULL_STRATEGY_PENDING",
            "source_commit": "9bb7dab5df53203e69f60336749551a40767af0b",
            "official_engine_commit": official["commit"],
            "real_source_numerical_cases": 6,
            "real_source_outer_fit_calls": 12,
            "coefficient_models_per_outer_fit": 101,
            "actual_native_indicator_rows": 4000,
            "local_full_retraining_parity_rows": 6000,
            "new_financial_runs": 0,
            "new_native_wallets": 0,
            "new_qualified_alpha": 0,
            "predictive_power_validated": False,
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
            "scope": "Source/numerical/feature-interface and evidence integrity only",
            "independent_verification_sha256": sha(HERE / "independent_verification.json"),
            "official_manifest_sha256": OFFICIAL_SHA,
            "official_source_files_verified": len(official["files"]),
            "source_git_blobs_verified": source_count,
            "prior_studies_verified": parents,
            "dependency_manifests_verified": dependencies,
            "unchanged_storage_content_verified_paths": len(storage["replaced_paths"]),
            "local_module_sha256": sha(ROOT / "freqtrade/data/wavelet_cpu.py"),
            "financial_accounts_completed": 0,
            "profitability_certified": False,
            "full_original_strategy_reproduction": False,
            "goal_complete": False,
        },
    )
    external = ROOT / "user_data/crypto_quant" / HERE.name
    assert not list(external.rglob("*.zip"))
    files = [
        p
        for base in [HERE, external]
        for p in base.rglob("*")
        if p.is_file()
        and p.name != "artifact_manifest.json"
        and "__pycache__" not in p.parts
        and ".pytest_cache" not in p.parts
    ]
    files.append(ROOT / "freqtrade/data/wavelet_cpu.py")
    save(
        "artifact_manifest.json",
        {
            "sealed_at": datetime.now(UTC).isoformat(),
            "files": [
                {"path": str(p), "bytes": p.stat().st_size, "sha256": sha(p)}
                for p in sorted(set(files))
            ],
            "excluded": (
                "Bytecode/test caches; reused dependencies and prior studies verified by reference"
            ),
            "new_financial_wallets": 0,
            "goal_complete": False,
        },
    )
    for row in read(HERE / "artifact_manifest.json")["files"]:
        assert sha(row["path"]) == row["sha256"], row["path"]
    print(
        json.dumps(
            {
                "passed": True,
                "sealed_files": len(set(files)),
                "manifest_sha256": sha(HERE / "artifact_manifest.json"),
                "new_financial_wallets": 0,
                "goal_complete": False,
            }
        )
    )


if __name__ == "__main__":
    main()
