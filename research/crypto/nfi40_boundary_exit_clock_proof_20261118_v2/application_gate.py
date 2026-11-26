"""Supplemental evidence classification only; never imports or changes an auditor."""

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALLOWED_ACCOUNT = "nfi40_x8_500_cost15"
ENTRY_STATUS = "PASSED_NARROW_NATIVE_DCA_CREATION_CLOCK_CONSTRAINT"
EXIT_STATUS = "PASSED_NARROW_NATIVE_FINAL_EXIT_CREATION_CLOCK_CONSTRAINT"


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def validate_semantics(account, config, process, result, grid, entry, exit_):
    if account != ALLOWED_ACCOUNT or result.get("candidate") != account:
        raise ValueError("Account has no individually frozen applicability certificate")
    if process.get("returncode") != 0 or result.get("orders_enabled") is not False:
        raise ValueError("Actual successful historical process required")
    if config.get("runmode") != "backtest" or config.get("dry_run") is not True:
        raise ValueError("Historical backtest only")
    if config.get("timeframe") != "5m" or config.get("timeframe_detail", "") != "":
        raise ValueError("Exact 5m/no-detail contract required")
    if config.get("timerange") != "1735689600-1788220799" or config.get("dry_run_wallet") != 500 or config.get("fee") != 0.0015:
        raise ValueError("Registered account identity/economics differs")
    if config.get("unfilledtimeout") != {"entry": 3, "exit": 2, "exit_timeout_count": 0, "unit": "minutes"}:
        raise ValueError("Exact timeout contract required")
    if any(config.get("order_types", {}).get(side) != "limit" or config.get("order_time_in_force", {}).get(side) != "GTC" for side in ("entry", "exit")):
        raise ValueError("Exact order contract required")
    if grid.get("passed") is not True or grid.get("pair_count") != 40 or grid.get("pairs_in_order") != config["exchange"]["pair_whitelist"] or grid.get("all_calendar_steps_seconds") != 300 or grid.get("all_fills_on_5m_grid") is not True or grid.get("startup_candles") != 800:
        raise ValueError("Complete individual input/fill grid evidence required")
    if entry.get("status") != ENTRY_STATUS or exit_.get("status") != EXIT_STATUS:
        raise ValueError("Both separately executed mechanical proofs required")
    if len(entry.get("actual_boundary_DCA", [])) != 3 or len(exit_.get("actual_boundary_final_exits", [])) != 2:
        raise ValueError("This account's boundary inventory differs")
    records = []
    for kind, rows in (("additional_entry", entry["actual_boundary_DCA"]), ("ordinary_final_exit", exit_["actual_boundary_final_exits"])):
        for row in rows:
            if row.get("creation_exported") is not False or row.get("fill_timestamp_ms") != row.get("inferred_creation_timestamp_ms"):
                raise ValueError("Metadata/inference boundary differs")
            records.append({**row, "event_kind": kind, "classification": "CONDITIONAL_NATIVE_SOURCE_CONSTRAINT", "creation_metadata_available": False})
    return records


def apply():
    frozen = read(HERE / "freeze.json")
    for path, digest in frozen["files"].items():
        if sha(path) != digest:
            raise ValueError("Frozen dependency differs: " + path)
    protocol = read(HERE / "protocol.json")
    predecessor = Path(protocol["entry_proof_directory"])
    entry, exit_ = read(predecessor / "validation.json"), read(HERE / "validation.json")
    for directory, value in ((predecessor, entry), (HERE, exit_)):
        process = read(directory / "process_receipt.json")
        if process.get("observed_wait_exit_code") != 0 or process.get("validation_sha256") != sha(directory / "validation.json") or process.get("freeze_sha256") != sha(directory / "freeze.json") or value.get("freeze_sha256") != sha(directory / "freeze.json"):
            raise ValueError("Object proof lacks bound observed terminal success")
    out = Path(protocol["effective_config"]).parent
    config, process, result = read(out / "effective_config.json"), read(out / "process_receipt.json"), read(out / "result.json")
    if process.get("result_sha256") != sha(out / "result.json"):
        raise ValueError("Actual financial process/result hash differs")
    if any(value.get("actual_effective_config_sha256") != sha(out / "effective_config.json") for value in (entry, exit_)):
        raise ValueError("Proof is not bound to the actual effective configuration")
    source_sha = sha(protocol["actual_source"])
    if source_sha != protocol["required_source_sha256"] or source_sha != sha(protocol["original_source"]):
        raise ValueError("Actual and original source differ")
    vendor = Path(protocol["vendor_root"])
    manifest = read(protocol["vendor_manifest"])
    if len(manifest["files"]) != 595:
        raise ValueError("Wrong owner manifest")
    for name, digest in manifest["files"].items():
        if sha(vendor / name) != digest:
            raise ValueError("Official owner changed: " + name)
    grid = read(HERE / "input_grid_validation.json")
    if grid.get("native_zip_sha256") != sha(protocol["native_zip"]) or grid.get("effective_config_sha256") != sha(protocol["effective_config"]):
        raise ValueError("Input grid is not bound to this actual archive/config")
    if len(grid.get("rows", [])) != 40:
        raise ValueError("Missing input grid pairs")
    for row in grid["rows"]:
        if row["sha256"] != frozen["files"].get(row["path"]):
            raise ValueError("Grid evidence lacks frozen raw input identity")
    records = validate_semantics(protocol["actual_account"], config, process, result, grid, entry, exit_)
    return {
        "schema": "supplemental-native-boundary-clock-applicability.v1",
        "account": protocol["actual_account"], "classification": "CONDITIONAL_NATIVE_SOURCE_CONSTRAINT",
        "events": records, "source_sha256": source_sha, "effective_config_sha256": sha(protocol["effective_config"]),
        "native_zip_sha256": sha(protocol["native_zip"]), "owner_manifest_sha256": sha(protocol["vendor_manifest"]),
        "input_grid_receipt_sha256": sha(HERE / "input_grid_validation.json"),
        "application_code_sha256": sha(HERE / "application_gate.py"), "application_freeze_sha256": sha(HERE / "freeze.json"),
        "creation_metadata_available": False, "numeric_cash_nav_funding_or_economic_gates_changed": False,
        "full_account_independent_audit_claim": False, "old_unknown_receipts_modified": False,
        "eight_bps_inherited": False, "orders_enabled": False,
    }


if __name__ == "__main__":
    value = apply()
    (HERE / "application_scope_receipt.json").write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"classification": value["classification"], "events": len(value["events"]), "numeric_audit_changed": False}))
