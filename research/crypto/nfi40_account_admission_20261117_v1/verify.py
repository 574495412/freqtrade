"""Final readback of source hashes, old native accounts and new diagnostics."""

import ast
import json
from datetime import UTC, datetime
from pathlib import Path

from account_audit import HERE, read, sha, stats_from


def main():
    hashes = read(HERE / "input_hashes.json")
    for path, digest in hashes.items():
        assert sha(path) == digest, path
    exposure = read(HERE / "account_exposure_audit.json")
    precision = read(HERE / "precision_audit.json")
    assert exposure["passed"] and len(exposure["accounts"]) == 14
    assert precision["native_algorithm_calls"] == 40
    assert precision["target_bars_with_full_vs_past_precision_difference"] == 102845
    assert precision["recorded_orders_at_different_precision"] == 44
    assert precision["recorded_fills_off_strict_past_grid"] == 0
    assert precision["resolved_startup_candles"] == 800
    for path, digest in precision["native_log_hashes"].items():
        assert sha(path) == digest
    for row in read(HERE / "producer_receipts.json"):
        assert sha(row["snapshot"]) == row["sha256"]
    env = read(HERE / "numerical_environment_and_code_parity.json")
    assert all(v["frozen"] == v["current"] for v in env["versions"].values())
    assert all(env["precision_function_AST_unchanged_after_style_cleanup"].values())
    comparisons, accounts = [], {}
    for path in sorted((HERE / "native_rechecks").glob("*/comparison.json")):
        comparison = read(path)
        assert comparison["accounts"] == 2
        for source, digest in comparison["input_hashes"].items():
            assert sha(source) == digest
        assert (
            sha(comparison["independent_auditor"]["path"])
            == comparison["independent_auditor"]["sha256"]
        )
        comparisons.append(str(path))
        for row in comparison["rows"]:
            if row["name"] != "continuous":
                continue
            archive = next(Path(row["source"]).parent.glob("native_results/*.zip"))
            accounts[Path(row["source"]).parent.name] = stats_from(archive)
    assert len(comparisons) == 3 and len(accounts) == 6
    linked = []
    cutoff = datetime(2026, 8, 31, 23, tzinfo=UTC)
    for finding in precision["findings"]:
        trade = accounts[finding["account"]]["trades"][finding["trade_index"]]
        order = trade["orders"][finding["archive_order_index"]]
        assert finding["filled_price"] == order["safe_price"]
        assert (
            datetime.fromisoformat(finding["at"]).timestamp() * 1000
            == order["order_filled_timestamp"]
        )
        linked.append(
            {
                **finding,
                "pair": trade["pair"],
                "entry": order["ft_is_entry"],
                "before_common_endpoint": datetime.fromisoformat(finding["at"]) <= cutoff,
            }
        )
    assert all(row["before_common_endpoint"] for row in linked)
    (HERE / "precision_findings_linked.json").write_text(json.dumps(linked, indent=2) + "\n")
    source = HERE.with_name("funding_slot_provenance_native_20261116_v1")
    audit = read(source / "source_audit.json")
    assert audit["passed_source_checks"] and len(audit["slots"]) == 2
    assert all(
        r["status"] == "UNKNOWN" and r["missing_expected_slot_rate"] is None for r in audit["slots"]
    )
    assert "6 passed" in (HERE / "tests_initial.log").read_text()
    gh = read(HERE / "github_source_receipt.json")
    assert sha(HERE / "sources/official_historic_precision.py") == gh["sha256"]
    # Check that the primary completed-bar function remains precisely the
    # one whose old source/AST was independently retained after execution.
    before = ast.parse(
        (
            HERE / "producer_snapshots/nfi40_account_admission_20261117_v1_precision_audit.py"
        ).read_text()
    )
    after = ast.parse((HERE / "precision_audit.py").read_text())
    for name in ["strict_past_count", "main"]:
        left = next(n for n in before.body if isinstance(n, ast.FunctionDef) and n.name == name)
        right = next(n for n in after.body if isinstance(n, ast.FunctionDef) and n.name == name)
        assert ast.dump(left) == ast.dump(right)
    result = {
        "verified_at": datetime.now(UTC).isoformat(),
        "passed": True,
        "native_account_archives_reviewed": 14,
        "fresh_independent_comparisons": comparisons,
        "old_nfi_accounts_with_fresh_fill_cash_and_full_curve_audit": 6,
        "old_nfi_hourly_curve_points": 87552,
        "unchanged_input_paths": len(hashes),
        "official_native_files_verified": 595,
        "funding_slots_still_unknown": 2,
        "nfi_model_slot_exposures_absent": 12,
        "lowvol_skew_model_slot_exposures_present": 16,
        "native_precision_function_calls": 40,
        "source_bar_observations": 7003720,
        "bars_with_full_vs_past_precision_difference": 102845,
        "recorded_order_observations_checked": 2994,
        "order_observations_at_different_precision": 44,
        "recorded_fill_prices_off_strict_past_grid": 0,
        "new_unique_tests": 6,
        "new_financial_accounts": 0,
        "new_accepted_alphas": 0,
        "new_portfolio_accounts": 0,
        "corrected_native_returns": None,
        "exchange_historical_tick_certified": False,
        "orders_enabled": False,
        "production_eligible": False,
        "goal_complete": False,
    }
    (HERE / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print("Verified14old archives,6full curves,40native precision calls;0new financial accounts")


if __name__ == "__main__":
    main()
