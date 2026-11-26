"""Verify the technical checkpoint and unchanged parents;never certify returns."""

import hashlib
import json
from pathlib import Path

from bootstrap import HERE, ROOT, boot


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def main():
    engine = boot()
    for row in read(HERE / "source_receipts.json"):
        if "path" in row:
            assert sha(row["path"]) == row["sha256"]
    for key in ("current_owner_files", "native_owner_files"):
        for row in read(HERE / "ownership.json")[key]:
            assert sha(row["path"]) == row["sha256"]
    history = read(HERE / "native_history_admission.json")
    for key in ("actual_native_handler", "actual_native_loader"):
        assert history[key].startswith("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade/")
    assert history["rows"] == 25 and history["native_zero_volume_rows"] == 21
    assert history["native_history_exactly_equals_source"]
    assert read(HERE / "metadata_admission.json")["counts"] == {
        "btc_instruments.json": 1064,
        "eth_instruments.json": 884,
        "expired_instrument.json": 1,
    }
    independent = read(HERE / "independent_audit.json")
    assert independent["passed"] and not independent["candidate_module_imported"]
    assert independent["metadata_records_independently_checked"] == 1949
    assert independent["source_chart_and_unit_scalars_checked"] == 162
    assert "55 passed" in (HERE / "tests_final.log").read_text()
    for name in ("lint_production_final.log", "lint_research_final.log"):
        assert (HERE / name).read_text().strip() == "All checks passed!"
    old = HERE / "pre_delivery_extension"
    for name in ("metadata_admission.json", "native_history_admission.json", "unit_probe.json"):
        assert (old / name).read_bytes() == (HERE / name).read_bytes()
    parents = []
    for relative, expected in [
        (
            "crypto/macro_allocation_native_20261116_v1",
            "db926e5b2a13f86ffb359970a27e0b3eb2a29912971e5f94e8e0207fdf6c57ce",
        ),
        (
            "crypto/macro_transmission_source_native_20261116_v1",
            "5ff4428eaa4e7ab9ea45a858f7f6834a883dbfc4b9025b0309b80e5248318684",
        ),
        (
            "equity/split_notice_admission_native_20261116_v1",
            "d69c22c260e5a219146a88869e9cfdeb09226e859a976b4e3303887b4b6d9fef",
        ),
    ]:
        path = ROOT / "research" / relative / "artifact_manifest.json"
        assert sha(path) == expected
        files = read(path)["files"]
        for row in files:
            assert sha(row["path"]) == row["sha256"], row["path"]
        parents.append({"path": str(path), "sha256": expected, "verified_paths": len(files)})
    result = {
        "passed": True,
        "scope": "Original source/native input and unit engineering only",
        "engine": engine,
        "unique_boundary_tests": 55,
        "metadata_records": 1949,
        "source_chart_rows": 25,
        "source_zero_volume_rows": 21,
        "independent_chart_unit_scalars": 162,
        "parents": parents,
        "new_financial_backtests": 0,
        "new_accepted_independent_alphas": 0,
        "new_registry_definitions": 0,
        "new_factor_analysis": 0,
        "new_vibe_benches": 0,
        "new_portfolio_accounts": 0,
        "option_net_return": None,
        "option_cagr": None,
        "full_comparison_account_window_complete": False,
        "higher_comparable_net_return_established": False,
        "goal_complete": False,
        "real_orders_enabled": False,
        "production_eligible": False,
        "source_whole_notebook_executed": False,
    }
    (HERE / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
