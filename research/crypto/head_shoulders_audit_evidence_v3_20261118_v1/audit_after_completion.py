"""Reuse the frozen numeric audit with verified catalog and per-account clock evidence."""

import json
import time
from copy import deepcopy
from pathlib import Path

import pandas as pd
import psutil

from . import independent_account_audit_v3 as audit
from .evidence_bridge import CLASSIFICATION, digest
from .reconcile_completed_catalog import source_catalog


COST8 = "nfi40_x8_500_cost8"
CLOCK8 = audit.HERE.parent / "nfi40_8bp_clock_applicability_preparation_20261118_v1"
DEFAULT_LOAD = audit.load_conditional_scope
DEFAULT_SUPPLEMENT = audit.supplement_boundary_evidence


def load_scope(candidate, directory, archive, stats):
    if candidate["name"] != COST8:
        return DEFAULT_LOAD(candidate, directory, archive, stats)
    certificate = CLOCK8 / "applied_after_terminal/applicability.json"
    receipt = audit.read(certificate)
    assert receipt["candidate"] == COST8 and candidate["capital"] == 500 and candidate["cost_bps"] == 8
    assert receipt["status"] == "CONDITIONAL_NATIVE_SOURCE_CONSTRAINT_SCOPE"
    assert receipt["metadata_restored"] is False and receipt["unknown_boundary_events"] == 0
    expected = {
        archive: "actual_archive_sha256",
        directory / "effective_config.json": "actual_effective_config_sha256",
        directory / "strategies/NostalgiaForInfinityX8.py": "actual_source_sha256",
        directory / "process_receipt.json": "actual_terminal_process_sha256",
        directory / "result.json": "actual_result_sha256",
        CLOCK8 / "freeze.json": "freeze_sha256",
        CLOCK8 / "application_release.json": "root_release_sha256",
    }
    assert all(audit.sha(path) == receipt[field] for path, field in expected.items())
    assert sum(len(t["orders"]) for t in stats["trades"]) == receipt["all_fills_examined"]
    events = {}
    for record in receipt["boundary_events"]:
        key = record["trade_index"], record["order_index"]
        assert key not in events and record["classification"] == CLASSIFICATION
        trade = stats["trades"][key[0]]
        order = trade["orders"][key[1]]
        assert trade["pair"] == record["pair"]
        assert order["order_filled_timestamp"] == record["filled_ms"] == record["conditional_inferred_creation_ms"]
        assert order["ft_is_entry"] is record["is_entry"]
        assert trade["exit_reason"] == record["native_exit_reason"]
        if record["event_role"] == "ordinary_final_exit":
            assert not order["ft_is_entry"] and key[1] == len(trade["orders"]) - 1
            assert trade["exit_reason"].startswith("exit_")
        else:
            assert record["event_role"] == "additional_entry" and order["ft_is_entry"] and key[1] > 0
        events[key] = {**record, "bound_trade_digest": digest(trade)}
    return {
        "candidate": COST8, "events": events, "certificate_sha256": audit.sha(certificate),
        "certificate_path": str(certificate), "classification": CLASSIFICATION,
    }


def supplement(candidate, number, trade, evidence, scope):
    if candidate["name"] != COST8:
        return DEFAULT_SUPPLEMENT(candidate, number, trade, evidence, scope)
    assert scope["candidate"] == COST8
    result = deepcopy(evidence)
    result["raw_metadata_status"] = evidence["status"]
    result["conditional_source_constraint_applied"] = False
    for row in result["boundary_fills"]:
        row["raw_metadata_status"] = row["status"]
        event = scope["events"].get((number, row["order_index"]))
        if event is None:
            continue
        assert digest(trade) == event["bound_trade_digest"] and trade["pair"] == event["pair"]
        assert row["filled_ms"] == event["filled_ms"] and row["is_entry"] is event["is_entry"]
        assert row["created_ms"] is None and row["raw_metadata_status"] == "UNKNOWN"
        row.update(
            status=CLASSIFICATION, reason="Creation clock follows fixed native source constraints",
            conditional_inferred_creation_ms=event["conditional_inferred_creation_ms"],
            conditional_evidence_sha256=scope["certificate_sha256"],
        )
        result["conditional_source_constraint_applied"] = True
    result["status"] = "UNKNOWN" if any(row["status"] == "UNKNOWN" for row in result["boundary_fills"]) else "VERIFIED"
    return result


def wait_for_native_financial_receipts(plan):
    state_file = audit.BASE_STUDY / ".process.json"
    while True:
        current = audit.read(state_file)
        if current["state"] == "COMPLETED":
            break
        pid = current["controller_pid"]
        if not psutil.pid_exists(pid):
            raise RuntimeError("Controller ended without four successful bound receipts")
        controller = psutil.Process(pid)
        if "research.crypto.head_shoulders_financial_native_20261117_v3.execute" not in controller.cmdline():
            raise RuntimeError("Prior controller identity differs")
        time.sleep(5)
    complete = audit.read(audit.BASE_STUDY / "execution_complete.json")
    assert complete["native_accounts_completed"] == 4
    assert set(complete["completed"]) == {c["name"] for c in plan["candidates"]}


def main():
    freeze = audit.read(audit.HERE / "completed_audit_freeze.json")
    assert all(audit.sha(path) == sha for path, sha in freeze["files"].items())
    old = audit.read(audit.HERE / "independent_audit_freeze_v3.json")
    assert all(audit.sha(path) == sha for path, sha in old["files"].items())
    plan = audit.read(audit.BASE_STUDY / "plan.json")
    protocol = audit.read(audit.BASE_STUDY / "independent_audit_protocol.json")
    assert audit.read(audit.verified(protocol["input_validation"]))["passed"]
    assert audit.read(audit.verified(protocol["precision_validation"]))["passed"]
    wait_for_native_financial_receipts(plan)
    nfi = audit.read(audit.verified(plan["nfi500_dependency"]["plan"]))
    audit.source_catalog = source_catalog
    audit.load_conditional_scope = load_scope
    audit.supplement_boundary_evidence = supplement
    output = audit.HERE / "audited_complete_accounts"
    output.mkdir(exist_ok=False)
    hs, nfi_results, paths = [], {}, {}
    for owners, is_hs in ((plan, True), (nfi, False)):
        for candidate in owners["candidates"]:
            name = candidate["name"]
            row, nav = audit.account(candidate, owners, protocol, output / name, hs=is_hs)
            paths[name] = nav
            if is_hs:
                hs.append(row)
            else:
                nfi_results[candidate["cost_bps"]] = row
            print(name, row["status"], row["periods"][0]["net_return"], flush=True)
    combinations = []
    for benchmark in plan["benchmark_nfi1000"]:
        cost = benchmark["cost_bps"]
        hs_name = f"hs40_500_cost{cost}"
        nfi_name = f"nfi40_x8_500_cost{cost}"
        base = pd.read_parquet(audit.verified(benchmark["hourly_curve"]))
        baseline = base.set_index(pd.DatetimeIndex(base.date).as_unit("ns")).hourly_mark_equity
        hs_row = next(row for row in hs if row["candidate"] == hs_name)
        nav, result = audit.fixed_combo(
            paths[hs_name], paths[nfi_name], baseline, plan["periods"],
            hs_row["passed"] and nfi_results[cost]["passed"],
        )
        result["cost_bps"] = cost
        combinations.append(result)
        nav.rename("fixed_combo_nav").to_frame().to_parquet(output / f"fixed_combo_cost{cost}.parquet")
    audit.save(output / "admission.json", {
        "hs_accounts": hs, "nfi_accounts": nfi_results,
        "standalone": audit.standalone_gate(hs), "fixed_combo": combinations,
        "both_cost_combo_passed": all(row["passed"] for row in combinations),
        "cash_funding_nav_metrics_and_economic_gate_owners": str(Path(audit.__file__).resolve()),
        "financial_reruns": 0, "orders_enabled": False, "production_eligible": False,
    })
    print(json.dumps({"completed": len(hs) + len(nfi_results), "combination_passed": all(r["passed"] for r in combinations)}))


if __name__ == "__main__":
    main()
