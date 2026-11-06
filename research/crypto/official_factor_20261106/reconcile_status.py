"""Reconcile historical and official research identities without claiming promotion."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD = ROOT / "research/crypto/rd_program_20261105"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    verified = {}
    for folder in (OLD, HERE):
        for name, digest in read(folder / "freeze.json")["files"].items():
            assert sha(ROOT / name) == digest, name
            verified[name] = digest
    old = read(OLD / "p1/native/native_scorecard.json")
    for receipt in old["input_receipts"]:
        assert sha(ROOT / receipt["path"]) == receipt["sha256"], receipt["path"]
        verified[receipt["path"]] = receipt["sha256"]
    controls = read(OLD / "p1/control_scorecard.json")
    assert len(old["rows"]) == old["native_account_count"] == 16
    assert len(controls["rows"]) == old["control_account_count"] == 16
    assert len(old["determinism"]) == 2 and all(r["passed"] for r in old["determinism"])
    official = read(HERE / "results.json")
    assert len(official["rows"]) == 24 and all(r["profit_total"] < 0 for r in official["rows"])
    for row in official["rows"]:
        for key in ("archive", "receipt"):
            assert sha(ROOT / row[key]) == row[key + "_sha256"], row[key]
    diag = read(HERE / "recursive_results.json")
    for row in diag["rows"]:
        assert sha(ROOT / row["log"]) == row["log_sha256"]
    seam = read(OLD / "execution_extension/native_seam_verification.json")
    sources = [OLD / "p1/native/native_scorecard.json", OLD / "p1/control_scorecard.json",
               OLD / "candidate_readiness_execution_update_20261106.json",
               OLD / "data_extension/event_source_audit.json",
               OLD / "execution_extension/native_seam_verification.json",
               ROOT / "freqtrade/plugins/protections/max_drawdown_protection.py",
               ROOT / "freqtrade/freqtradebot.py", HERE / "results.json",
               HERE / "recursive_results.json", Path(__file__)]
    verified.update({str(p.relative_to(ROOT)): sha(p) for p in sources})
    state = {
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "overall_goal_complete": False,
        "current_direction": "Official methodology; unchanged upstream examples completed and rejected",
        "scope_question_pending": "Whether official-only permits implementing the already specified F1/F2 using native framework APIs",
        "historical_P1": {"native_accounts": 16, "control_accounts": 16,
                          "determinism_checks_passed": 2, "frozen_inputs_match": True,
                          "not_replaced_by_official_example_accounts": True},
        "official_examples": {"accounts": 24, "profitable_accounts": 0,
                              "recursive_commands_used": 2, "new_diagnostic_budget_remaining": 6,
                              "new_program_account_budget_used": 24, "new_program_account_budget_remaining": 56,
                              "RD1_applied": False, "F1_F2_tested": False},
        "original_F1_F2": {"financial_runs": 0, "economic_validation_complete": False,
                           "execution_certification_complete": False,
                           "historical_receipt_and_depth_full_window": "NOT_VERIFIED",
                           "bar_cost_scenario_method": "Permitted in original roadmap, distinct from executable-depth certification",
                           "paired_RD1_controls_complete": False},
        "native_integration": {"synthetic_tests_historically_passed": seam["tests_run"],
                               "tests_rerun_in_this_reconciliation": 0,
                               "mocked_boundaries": seam["mocked_boundaries"],
                               "whole_native_bot_integrated": False},
        "official_risk_compatibility": {
            "MaxDrawdown_uses_closed_trades_even_in_equity_mode": True,
            "MaxDrawdown_action": "Entry lock, not RD1 floating-equity liquidation and permanent latch",
            "confirm_trade_entry_scope": "execute_entry invokes it in initial mode; not a universal atomic reservation hook",
            "RD1_equivalence_established": False},
        "completion_requirements": [
            {"requirement": "Full historical availability/depth/rule/event evidence", "status": "INCOMPLETE"},
            {"requirement": "RD1 native lifecycle, after-fill and database transaction integration", "status": "INCOMPLETE_SYNTHETIC_ONLY"},
            {"requirement": "F1/F2 matched cost and funding account experiments", "status": "NOT_RUN"},
            {"requirement": "Retain v1, 32 P1 accounts and 2 determinism receipts", "status": "IDENTITIES_VERIFIED"},
            {"requirement": "Do not repeat exhausted old 8 diagnostics", "status": "PRESERVED_SEPARATE_OFFICIAL_BUDGET"},
            {"requirement": "Do not claim improved returns from tests/reports", "status": "NO_IMPROVEMENT_CLAIM"}],
        "new_financial_runs": 0, "new_tests_or_diagnostic_commands": 0,
        "profit_improvement_established": False, "production_eligible": False,
        "verified_source_identities": verified,
        "scope": "Read-only identity and source-contract reconciliation; no runtime or secret inspection, no proof of external data absence"
    }
    with (HERE / "current_status.json").open("x") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
    print(json.dumps({"verified_identities": len(verified), "original_accounts": 32,
                      "official_accounts": 24, "goal_complete": False}))


if __name__ == "__main__":
    main()
