"""Post-run scope/integrity and complete synthetic-lifecycle verification."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def verify_manifest(path, expected, count):
    assert sha(path) == expected
    manifest = load(path)
    rows = manifest["files"]
    if isinstance(rows, list):
        assert len(rows) == count
        for row in rows:
            assert sha(row["path"]) == row["sha256"], row["path"]
    else:
        assert len(rows) == count
        for name, digest in rows.items():
            target = Path(name) if Path(name).is_absolute() else path.parent / name
            assert sha(target) == digest, str(target)
    return {"path": str(path), "sha256": expected, "verified_paths": count}


def main():
    frozen = load(HERE / "implementation_freeze.json")
    assert len(frozen["files"]) == 32
    for path, digest in frozen["files"].items():
        assert sha(path) == digest, path
    engine = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")
    source = load(engine.parent / "FREQTRADE_SOURCE.json")
    assert source["commit"] == "c051e9065c5b677f03817dd1f83cb980af1ed684"
    assert len(source["files"]) == 595
    for path, digest in source["files"].items():
        assert sha(engine / path) == digest, path
    old = HERE.parent / "option_premium_admission_native_20261116_v1"
    parent = verify_manifest(
        old / "artifact_manifest.json",
        "11d11445314a6ad75e160adc1989f11d483fe1466f685954bd53a3783951d0d2",
        72,
    )
    other = []
    for name, digest, count in [
        (
            "macro_allocation_native_20261116_v1",
            "db926e5b2a13f86ffb359970a27e0b3eb2a29912971e5f94e8e0207fdf6c57ce",
            398,
        ),
        (
            "macro_transmission_source_native_20261116_v1",
            "5ff4428eaa4e7ab9ea45a858f7f6834a883dbfc4b9025b0309b80e5248318684",
            43,
        ),
    ]:
        other.append(verify_manifest(HERE.parent / name / "artifact_manifest.json", digest, count))
    runs = load(HERE / "execution_receipt.json")
    assert len(runs) == 10 and len({r["pid"] for r in runs}) == 10
    assert all(r["returncode"] == 0 and r["ended_at"] >= r["started_at"] for r in runs)
    assert {(r["currency"], r["case"]) for r in runs} == {
        (currency, case)
        for currency in ["BTC", "ETH"]
        for case in ["long", "short", "breach", "pending", "full_start"]
    }
    audit = load(HERE / "independent_audit.json")
    assert audit["status"] == "passed" and audit["boundaries"] == 66 and audit["actual_fills"] == 26
    assert audit["exact_coin_scalars"] == 726 and audit["full_start_zips"] == 2
    full = []
    for row in runs:
        trace = load(Path(row["folder"]) / "native_trace.json")
        if row["case"] != "full_start":
            continue
        contract = load(Path(row["folder"]) / "option_contract.json")
        start = datetime.fromisoformat(contract["starts"])
        assert len(trace["snapshots"]) == 16
        for n, snap in enumerate(trace["snapshots"]):
            expected = start + timedelta(hours=n // 2)
            assert datetime.fromisoformat(snap["at"]) == expected
            assert datetime.fromisoformat(snap["state"]["at"]) == expected
            assert snap["phase"] == ("before" if n % 2 == 0 else "after")
        assert trace["snapshots"][0]["native_orders"] == []
        assert trace["snapshots"][-1]["state"]["positions"] == []
        assert trace["actual_fill_count"] == 4
        zip_path = Path(trace["actual_zip"])
        assert zip_path.is_file()
        full.append(
            {
                "currency": row["currency"],
                "zip": str(zip_path),
                "zip_sha256": sha(zip_path),
                "native_fills": 4,
                "closed_trades": 2,
                "boundaries": 16,
            }
        )
    tests = (HERE / "tests_final.log").read_text()
    assert "28 passed" in tests and "failed" not in tests
    assert "All checks passed!" in (HERE / "lint_production_final.log").read_text()
    result = {
        "verified_at": datetime.now(UTC).isoformat(),
        "native_engine_files": 595,
        "pre_final_frozen_paths": 32,
        "parent_option_checkpoint": parent,
        "other_parent_checkpoints": other,
        "actual_final_native_processes": 10,
        "actual_fill_count": 26,
        "actual_boundaries": 66,
        "independent_exact_coin_scalars": 726,
        "full_start": full,
        "tests_passed": 28,
        "original_reference_account_window": ["2025-01-01T00:00:00Z", "2026-08-31T23:00:00Z"],
        "new_historical_option_accounts": 0,
        "new_accepted_independent_alphas": 0,
        "new_factor_analysis": 0,
        "new_registry_definitions": 0,
        "new_vibe_benches": 0,
        "new_portfolio_accounts": 0,
        "option_net_return": None,
        "option_cagr": None,
        "full_comparison_account_window_complete": False,
        "higher_comparable_net_return_established": False,
        "goal_complete": False,
        "liquidation_or_expiry_or_hedge_account_certified": False,
        "real_orders_enabled": False,
        "production_eligible": False,
    }
    (HERE / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        "VERIFIED:595native,72parent,10processes,26fills,66boundaries;0historicalaccounts",
        flush=True,
    )


if __name__ == "__main__":
    main()
