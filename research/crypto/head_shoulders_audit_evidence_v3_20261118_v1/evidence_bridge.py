"""Bound classification supplement; contains no cash/NAV/funding computation."""

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent
ALLOWED = "nfi40_x8_500_cost15"
CLASSIFICATION = "CONDITIONAL_NATIVE_SOURCE_CONSTRAINT"
EXCLUDED_EXITS = {"force_exit", "stop_loss", "trailing_stop_loss", "liquidation"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_bound_inputs(candidate, directory, archive_sha, effective_sha, source_sha, application, expected_directory):
    if candidate.get("name") != ALLOWED or candidate.get("capital") != 500 or candidate.get("cost_bps") != 15 or candidate.get("strategy") != "NostalgiaForInfinityX8":
        raise ValueError("Candidate is outside the separately frozen evidence scope")
    if Path(directory).resolve() != Path(expected_directory).resolve():
        raise ValueError("Account output directory differs")
    if application.get("account") != ALLOWED or application.get("classification") != CLASSIFICATION:
        raise ValueError("Wrong supplemental application identity")
    if archive_sha != application.get("native_zip_sha256"):
        raise ValueError("Actual archive differs from the bound certificate")
    if effective_sha != application.get("effective_config_sha256"):
        raise ValueError("Actual effective config differs from the bound certificate")
    if source_sha != application.get("source_sha256"):
        raise ValueError("Actual source differs from the bound certificate")
    if application.get("creation_metadata_available") is not False:
        raise ValueError("Certificate must retain missing creation metadata")
    if len(application.get("events", [])) != 5:
        raise ValueError("Expected precisely the individually certified five events")


def load_conditional_scope(candidate, directory, archive, stats):
    # Fail to inherit; no 8bp/1000 account path or result is opened here.
    if candidate.get("name") != ALLOWED:
        return None
    protocol = read(HERE / "evidence_protocol.json")
    gate_record = protocol["application_gate"]
    gate = Path(gate_record["path"])
    if sha(gate) != gate_record["sha256"]:
        raise ValueError("Frozen application interface changed")
    spec = importlib.util.spec_from_file_location("frozen_individual_boundary_application", gate)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # This performs the previously frozen actual process/source/owner/grid checks.
    application = module.apply()
    registered = protocol["application_scope_receipt"]
    if sha(registered["path"]) != registered["sha256"] or application != read(registered["path"]):
        raise ValueError("Supplement differs from the sealed individually reviewed certificate")
    validate_bound_inputs(candidate, directory, sha(archive), sha(Path(directory) / "effective_config.json"), sha(Path(directory) / "strategies/NostalgiaForInfinityX8.py"), application, protocol["expected_actual_output_directory"])
    with ZipFile(archive) as zipped:
        names = [n for n in zipped.namelist() if n.endswith(".json") and not n.endswith("_config.json")]
        if len(names) != 1:
            raise ValueError("Ambiguous bound archive payload")
        original = json.loads(zipped.read(names[0]))["strategy"][candidate["strategy"]]
    if digest(stats) != digest(original):
        raise ValueError("Audited trade payload is not the exact bound archive")
    events = {}
    for event in application["events"]:
        key = (event["trade_index"], event["order_index"])
        if key in events:
            raise ValueError("Duplicate event identity")
        trade = stats["trades"][event["trade_index"]]
        if event["event_kind"] == "ordinary_final_exit" and trade["exit_reason"] in EXCLUDED_EXITS:
            raise ValueError("Force/risk exit events cannot inherit this supplement")
        events[key] = {**event, "bound_trade_digest": digest(trade)}
    return {"candidate": ALLOWED, "events": events, "certificate_sha256": registered["sha256"], "certificate_path": registered["path"], "classification": CLASSIFICATION}


def supplement_boundary_evidence(candidate, number, trade, evidence, scope):
    result = deepcopy(evidence)
    result["raw_metadata_status"] = evidence["status"]
    result["conditional_source_constraint_applied"] = False
    for row in result["boundary_fills"]:
        row["raw_metadata_status"] = row["status"]
    if scope is None:
        return result
    if candidate.get("name") != ALLOWED or scope.get("candidate") != ALLOWED:
        raise ValueError("Scope cannot be transplanted to another candidate")
    for row in result["boundary_fills"]:
        event = scope["events"].get((number, row["order_index"]))
        if event is None:
            continue
        if event["event_kind"] == "ordinary_final_exit" and trade["exit_reason"] in EXCLUDED_EXITS:
            raise ValueError("Force/risk event cannot inherit ordinary clock evidence")
        if digest(trade) != event["bound_trade_digest"]:
            raise ValueError("Actual trade/fill payload differs from the bound event")
        if trade["pair"] != event["pair"] or row["filled_ms"] != event["fill_timestamp_ms"]:
            raise ValueError("Pair or fill clock differs")
        expected_entry = event["event_kind"] == "additional_entry"
        if row["is_entry"] is not expected_entry:
            raise ValueError("Entry/exit event role differs")
        if row["created_ms"] is not None or row["raw_metadata_status"] != "UNKNOWN":
            raise ValueError("This supplement requires the original missing metadata evidence")
        row.update(status=CLASSIFICATION, reason="Fixed native source constraint; exported creation metadata remains missing", conditional_inferred_creation_ms=event["inferred_creation_timestamp_ms"], conditional_evidence_sha256=scope["certificate_sha256"])
        result["conditional_source_constraint_applied"] = True
    result["status"] = "UNKNOWN" if any(r["status"] == "UNKNOWN" for r in result["boundary_fills"]) else "VERIFIED"
    return result


def verify_scope_coverage(scope, boundary_rows):
    if scope is None:
        return
    expected = set(scope["events"])
    observed = {(row["trade_index"], item["order_index"]) for row in boundary_rows for item in row["boundary_fills"] if item["status"] == CLASSIFICATION}
    if observed != expected:
        raise ValueError("Not all and only five registered boundary events were applied")


def account_evidence_fields(scope, boundary_rows, numeric_status):
    raw_unknown = any(row.get("raw_metadata_status") == "UNKNOWN" for row in boundary_rows)
    return {
        "raw_boundary_metadata_status": "UNKNOWN" if raw_unknown else "NO_UNRESOLVED_RAW_BOUNDARY_METADATA",
        "conditional_boundary_evidence_classification": CLASSIFICATION if scope is not None else None,
        "conditional_boundary_evidence_sha256": scope["certificate_sha256"] if scope is not None else None,
        "verification_scope": "COMPLETE_NUMERIC_CHECKS_PASSED_UNDER_BOUND_CONDITIONAL_NATIVE_SOURCE_CONSTRAINT" if scope is not None and numeric_status == "VERIFIED" else "ORIGINAL_AUDIT_STATUS_AND_LIMITS_RETAINED",
        "creation_metadata_restored": False,
    }
