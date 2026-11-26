"""Read only the registered15bp evidence; never account/main or pending8bp results."""

from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from . import evidence_bridge as bridge
from . import independent_account_audit_v3 as audit
from .verify_ast import check

HERE = Path(__file__).resolve().parent


def rejected(fn):
    try:
        fn()
    except (ValueError, AssertionError):
        return
    raise AssertionError("Invalid evidence accepted")


def guard(*args, **kwargs):
    raise AssertionError("No financial account audit/main or unregistered evidence read in fixtures")


def main():
    frozen = bridge.read(HERE / "independent_audit_freeze_v3.json")
    for name, digest in frozen["files"].items():
        assert bridge.sha(name) == digest, name
    ast_receipt = check()
    protocol = bridge.read(HERE / "evidence_protocol.json")
    directory = Path(protocol["expected_actual_output_directory"])
    application = bridge.read(protocol["application_scope_receipt"]["path"])
    archive = Path(protocol["actual_archive"])
    candidate = {"name": bridge.ALLOWED, "capital": 500, "cost_bps": 15, "strategy": "NostalgiaForInfinityX8"}
    with ZipFile(archive) as zipped:
        names = [n for n in zipped.namelist() if n.endswith(".json") and not n.endswith("_config.json")]
        assert len(names) == 1
        stats = json.loads(zipped.read(names[0]))["strategy"][candidate["strategy"]]
    passed, classified = [], []
    with patch.object(audit, "account", guard), patch.object(audit, "main", guard):
        scope = bridge.load_conditional_scope(candidate, directory, archive, stats)
        boundaries = []
        for number in sorted({key[0] for key in scope["events"]}):
            trade = stats["trades"][number]
            times = {value["fill_timestamp_ms"] for key, value in scope["events"].items() if key[0] == number}
            raw = audit.boundary_evidence(trade, times)
            assert raw["status"] == "UNKNOWN"
            supplement = bridge.supplement_boundary_evidence(candidate, number, trade, raw, scope)
            assert raw["status"] == "UNKNOWN"  # Inputs and historical evidence are not mutated.
            assert supplement["status"] == "VERIFIED" and supplement["raw_metadata_status"] == "UNKNOWN"
            for row in supplement["boundary_fills"]:
                assert row["created_ms"] is None and row["raw_metadata_status"] == "UNKNOWN"
                assert row["status"] == bridge.CLASSIFICATION
                classified.append({"trade_index": number, "pair": trade["pair"], **row})
            boundaries.append({"trade_index": number, **supplement})
        assert len(classified) == 5
        bridge.verify_scope_coverage(scope, boundaries)
        passed.append("exact_five_bound_events_classified_raw_metadata_unchanged")
        params = dict(candidate=candidate, directory=directory, archive_sha=bridge.sha(archive), effective_sha=bridge.sha(directory / "effective_config.json"), source_sha=bridge.sha(directory / "strategies/NostalgiaForInfinityX8.py"), application=application, expected_directory=directory)
        for name, mutation in (
            ("wrong_archive_rejected", lambda x: x.update(archive_sha="0" * 64)),
            ("wrong_effective_config_rejected", lambda x: x.update(effective_sha="0" * 64)),
            ("wrong_source_rejected", lambda x: x.update(source_sha="0" * 64)),
            ("wrong_candidate_rejected", lambda x: x["candidate"].update(name="nfi40_x8_1000_cost15")),
            ("wrong_actual_directory_rejected", lambda x: x.update(directory="/unregistered-account")),
        ):
            values = deepcopy(params)
            mutation(values)
            rejected(lambda: bridge.validate_bound_inputs(**values))
            passed.append(name)
        number = next(k[0] for k, value in scope["events"].items() if value["event_kind"] == "ordinary_final_exit")
        event_key = next(k for k, value in scope["events"].items() if k[0] == number and value["event_kind"] == "ordinary_final_exit")
        base_trade = stats["trades"][number]
        when = scope["events"][event_key]["fill_timestamp_ms"]
        for name, mutation in (
            ("wrong_fill_clock_rejected", lambda t: t["orders"][event_key[1]].update(order_filled_timestamp=when + 300000)),
            ("wrong_fill_amount_rejected", lambda t: t["orders"][event_key[1]].update(amount=t["orders"][event_key[1]]["amount"] + 1)),
            ("force_exit_rejected", lambda t: t.update(exit_reason="force_exit")),
            ("liquidation_rejected", lambda t: t.update(exit_reason="liquidation")),
            ("fabricated_creation_rejected", lambda t: t["orders"][event_key[1]].update(order_timestamp=when)),
        ):
            bad = deepcopy(base_trade)
            mutation(bad)
            times = {bad["orders"][event_key[1]]["order_filled_timestamp"]}
            evidence = audit.boundary_evidence(bad, times)
            rejected(lambda: bridge.supplement_boundary_evidence(candidate, number, bad, evidence, scope))
            passed.append(name)
        rejected(lambda: bridge.verify_scope_coverage(scope, boundaries[:-1]))
        passed.append("incomplete_five_event_coverage_rejected")
        raw = audit.boundary_evidence(base_trade, {when})
        for name in ("nfi40_x8_500_cost8", "nfi40_x8_1000_cost15"):
            changed = {**candidate, "name": name}
            # No read of those output/config/return files is possible in this branch.
            with patch.object(bridge, "read", guard):
                absent = bridge.load_conditional_scope(changed, "/never-read", "/never-read", {})
            assert absent is None
            unchanged = bridge.supplement_boundary_evidence(changed, number, base_trade, raw, absent)
            assert unchanged["status"] == "UNKNOWN" and unchanged["boundary_fills"][0]["created_ms"] is None
            passed.append(name + "_remains_UNKNOWN_without_reads")
        # HS path uses the old boundary function directly and is never supplemented.
        hs = audit.boundary_evidence(base_trade, {when}, strict_hs_proven=True)
        assert hs["boundary_fills"][0]["status"] == "STRICT_HS_NATIVE_SEQUENCE_PROVEN"
        passed.append("strict_HS_boundary_function_unchanged")
        for status in ("FAILED", "UNKNOWN", "VERIFIED"):
            fields = bridge.account_evidence_fields(scope, boundaries, status)
            assert fields["raw_boundary_metadata_status"] == "UNKNOWN" and not fields["creation_metadata_restored"]
            assert (fields["verification_scope"] == "COMPLETE_NUMERIC_CHECKS_PASSED_UNDER_BOUND_CONDITIONAL_NATIVE_SOURCE_CONSTRAINT") is (status == "VERIFIED")
        passed.append("conditional_full_numeric_claim_requires_original_VERIFIED_status")
    result = {"passed": True, "case_count": len(passed), "cases": passed, "classified_events": classified, "ast_equivalence": ast_receipt, "account_called": False, "audit_main_called": False, "native_account_started": False, "eight_bps_outputs_read": False, "cash_nav_funding_recomputed": False}
    (HERE / "fixture_validation.json").write_text(json.dumps(result, indent=2) + "\n")
    (HERE / "ast_equivalence.json").write_text(json.dumps(ast_receipt, indent=2) + "\n")
    print(json.dumps({"passed": True, "cases": len(passed), "classified_events": len(classified), "unchanged_functions": len(ast_receipt["unchanged_functions"])}))


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        import traceback
        (HERE / "fixture_failure.json").write_text(json.dumps({"type": type(error).__name__, "error": str(error), "traceback": traceback.format_exc()}, indent=2) + "\n")
        raise
