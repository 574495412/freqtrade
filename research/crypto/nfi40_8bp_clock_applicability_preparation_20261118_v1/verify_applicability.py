"""Future individually gated8bp clock classification; no financial account engine."""

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent
CANDIDATE = "nfi40_x8_500_cost8"
CONDITIONAL = "CONDITIONAL_NATIVE_SOURCE_CONSTRAINT"
EXCLUDED = {"force_exit", "stop_loss", "trailing_stop_loss", "liquidation", "emergency_exit"}


class Held(ValueError):
    pass


class LocalIO:
    def read_json(self, path):
        return json.loads(Path(path).read_text())

    def sha(self, path):
        digest = hashlib.sha256()
        with Path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(1048576), b""):
                digest.update(block)
        return digest.hexdigest()


def terminal_identity(io, output):
    """First output read is the actual terminal receipt; never infer from elapsed time."""
    output = Path(output)
    try:
        process = io.read_json(output / "process_receipt.json")
    except FileNotFoundError as error:
        raise Held("Actual8bp terminal process receipt is absent") from error
    if process.get("returncode") != 0:
        raise Held("Actual8bp process did not observably terminate successfully")
    expected = process.get("result_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise Held("Terminal process lacks result hash binding")
    result_path = output / "result.json"
    if io.sha(result_path) != expected:
        raise ValueError("Actual8bp process/result hash mismatch")
    result = io.read_json(result_path)
    if result.get("candidate") != CANDIDATE or result.get("strategy") != "NostalgiaForInfinityX8" or result.get("cost_bps_per_side") != 8 or result.get("orders_enabled") is not False:
        raise ValueError("Actual8bp result identity/mode differs")
    return {"process": process, "process_sha256": io.sha(output / "process_receipt.json"), "result_sha256": expected, "result": result}


def require_release(io, path, freeze_sha, terminal):
    try:
        release = io.read_json(path)
    except FileNotFoundError as error:
        raise Held("Root-reviewed after-terminal application release is absent") from error
    if release.get("candidate") != CANDIDATE or release.get("root_reviewed") is not True or release.get("apply_individual_clock_certificate") is not True or release.get("freeze_sha256") != freeze_sha or release.get("process_receipt_sha256") != terminal["process_sha256"] or release.get("result_sha256") != terminal["result_sha256"]:
        raise Held("Release does not bind this completed8bp account and frozen verifier")
    for name in ("native_zip_sha256", "effective_config_sha256"):
        if not isinstance(release.get(name), str) or len(release[name]) != 64:
            raise Held("Release lacks exact future artifact identity")
    return release


def validate_config(config, expected_pairs):
    exact = {"strategy": "NostalgiaForInfinityX8", "timeframe": "5m", "dry_run_wallet": 500, "fee": 0.0008, "runmode": "backtest", "dry_run": True, "trading_mode": "futures", "margin_mode": "isolated", "max_open_trades": 6, "tradable_balance_ratio": 0.99, "stake_amount": "unlimited", "timerange": "1735689600-1788220799", "backtest_cache": "none"}
    if any(config.get(k) != value for k, value in exact.items()):
        raise ValueError("Actual8bp effective configuration differs")
    if config.get("timeframe_detail", "") != "":
        raise ValueError("Native no-detail5m theorem does not cover this configuration")
    if config.get("unfilledtimeout") != {"entry": 3, "exit": 2, "exit_timeout_count": 0, "unit": "minutes"}:
        raise ValueError("Actual entry/exit timeout contract differs")
    if config.get("exchange", {}).get("pair_whitelist") != expected_pairs:
        raise ValueError("Actual ordered40 pool differs")
    if any(config.get("order_types", {}).get(side) != "limit" or config.get("order_time_in_force", {}).get(side) != "GTC" for side in ("entry", "exit")):
        raise ValueError("Actual limit/GTC contract differs")
    for key in ("ft_check_timed_out", "check_entry_timeout", "check_exit_timeout", "adjust_order_price", "custom_entry_price", "custom_exit_price"):
        if key in config or key in config.get("nfi_parameters", {}):
            raise ValueError("Strategy method override is outside the mechanical theorem")


def validate_hash_bindings(actual_archive_sha, actual_config_sha, actual_source_sha, release, protocol):
    if actual_archive_sha != release["native_zip_sha256"]:
        raise ValueError("Wrong actual8bp archive")
    if actual_config_sha != release["effective_config_sha256"]:
        raise ValueError("Wrong actual8bp effective config")
    if actual_source_sha != protocol["required_source_sha256"]:
        raise ValueError("Wrong actual copied NFI source")


def classify_trade(trade, number, funding_slots):
    """New inventory from this archive, never copy15bp event IDs/counts or results."""
    orders = trade.get("orders", [])
    if not orders or trade.get("is_open") is not False:
        raise ValueError("Closed native trade and exported orders required")
    previous = None
    for order in orders:
        stamp = order.get("order_filled_timestamp")
        if isinstance(stamp, bool) or not isinstance(stamp, int) or stamp % 300000:
            raise ValueError("Every exported fill must have exact5m-grid integer time")
        if previous is not None and stamp < previous:
            raise ValueError("Exported fill order is non-monotonic")
        if not isinstance(order.get("ft_is_entry"), bool) or order.get("amount", 0) <= 0:
            raise ValueError("Unsupported exported order role/quantity")
        previous = stamp
    if not orders[0]["ft_is_entry"] or orders[0]["order_filled_timestamp"] != trade.get("open_timestamp") or orders[-1]["ft_is_entry"] or orders[-1]["order_filled_timestamp"] != trade.get("close_timestamp"):
        raise ValueError("Exported campaign entry/final clock identity differs")
    rows = []
    for index, order in enumerate(orders):
        filled = order["order_filled_timestamp"]
        if filled not in funding_slots:
            continue
        status, reason = "UNKNOWN", None
        if index == 0:
            role, reason = "initial_entry", "INITIAL_ENTRY_SPECIAL_PATH_NOT_COVERED"
        elif order["ft_is_entry"]:
            role = "additional_entry"
            status = CONDITIONAL
        elif index != len(orders) - 1:
            role, reason = "partial_exit", "PARTIAL_EXIT_SPECIAL_PATH_NOT_COVERED"
        elif trade.get("exit_reason") in EXCLUDED:
            role, reason = "force_or_risk_final_exit", "FORCE_OR_RISK_EXIT_PATH_NOT_COVERED"
        elif not isinstance(trade.get("exit_reason"), str) or not trade["exit_reason"].startswith("exit_"):
            role, reason = "unclassified_final_exit", "UNREGISTERED_EXIT_REASON_PATH"
        else:
            role = "ordinary_final_exit"
            status = CONDITIONAL
        created = order.get("order_timestamp")
        if created is not None:
            status, reason = "UNKNOWN", "EXPORT_CREATION_METADATA_SCHEMA_REQUIRES_SEPARATE_REVIEW"
        rows.append({"trade_index": number, "pair": trade["pair"], "order_index": index, "event_role": role, "is_entry": order["ft_is_entry"], "filled_ms": filled, "created_ms": created, "raw_metadata_status": "UNKNOWN" if created is None else "PRESENT_NOT_CERTIFIED", "classification": status, "unknown_reason": reason, "conditional_inferred_creation_ms": filled if status == CONDITIONAL else None, "native_exit_reason": trade.get("exit_reason")})
    return rows


def apply_after_terminal():
    io = LocalIO()
    protocol = io.read_json(HERE / "protocol.json")
    frozen = io.read_json(HERE / "freeze.json")
    output = Path(protocol["expected_actual_output_directory"])
    # No actual8bp config/archive/source/return read precedes the receipt0/hash gate.
    terminal = terminal_identity(io, output)
    release = require_release(io, HERE / "application_release.json", io.sha(HERE / "freeze.json"), terminal)
    for name, expected in frozen["files"].items():
        if io.sha(name) != expected:
            raise ValueError("Frozen prerequisite changed: " + name)
    destination = HERE / "applied_after_terminal"
    destination.mkdir(exist_ok=False)
    try:
        archives = list((output / "native_results").glob("*.zip"))
        if len(archives) != 1:
            raise ValueError("Exactly one actual8bp archive required")
        config_path = output / "effective_config.json"
        source_path = output / "strategies/NostalgiaForInfinityX8.py"
        validate_hash_bindings(io.sha(archives[0]), io.sha(config_path), io.sha(source_path), release, protocol)
        config = io.read_json(config_path)
        validate_config(config, protocol["pairs"])
        bindings = {str(config_path): io.sha(config_path), str(source_path): io.sha(source_path), str(archives[0]): io.sha(archives[0])}
        for relative, record in protocol["research_support"].items():
            copied = output / "support" / relative
            if io.sha(copied) != record["sha256"]:
                raise ValueError("Actual copied support differs: " + relative)
            bindings[str(copied)] = record["sha256"]
        for record in protocol["runner_and_adapters"]:
            if io.sha(record["path"]) != record["sha256"]:
                raise ValueError("Owning runner/storage path changed")
            bindings[record["path"]] = record["sha256"]
        vendor = Path(protocol["vendor_root"])
        manifest = io.read_json(protocol["vendor_manifest"]["path"])
        if len(manifest["files"]) != 595:
            raise ValueError("Wrong official owner manifest")
        for name, digest in manifest["files"].items():
            if io.sha(vendor / name) != digest:
                raise ValueError("Fixed official owner changed: " + name)
        expected_engine_path = str(vendor / "freqtrade/__init__.py")
        if terminal["result"].get("engine_source") != expected_engine_path:
            raise ValueError("Actual successful result names a different owner")
        for record in protocol["mechanical_theorem_receipts"]:
            proof = io.read_json(record["path"])
            if io.sha(record["path"]) != record["sha256"] or proof.get("status") != record["expected_status"]:
                raise ValueError("Previously frozen mechanical theorem is unavailable")
        # Only after successful actual terminal and root release: date-only input proof.
        import pandas as pd
        start = pd.Timestamp(protocol["start"]) - pd.Timedelta(minutes=800 * 5)
        end = pd.Timestamp(protocol["end"])
        expected = pd.date_range(start, end, freq="5min", inclusive="left")
        calendars, grids = {}, []
        for pair in protocol["pairs"]:
            records = protocol["input_files"][pair]
            raw = Path(records["raw_5m"]["path"])
            dates = pd.DatetimeIndex(pd.read_feather(raw, columns=["date"]).date)
            selected = dates[(dates >= start) & (dates < end)]
            if not dates.is_unique or not dates.is_monotonic_increasing or not selected.equals(expected):
                raise ValueError("Actual full native input clock is incomplete: " + pair)
            fund = Path(records["funding_dates"]["path"])
            times = pd.DatetimeIndex(pd.read_feather(fund, columns=["date"]).date)
            millis = [int(v.timestamp() * 1000) for v in times]
            if not times.is_unique or not times.is_monotonic_increasing or any(v % 3600000 for v in millis):
                raise ValueError("Funding calendar outside fixed hourly-clock scope")
            calendars[pair] = set(millis)
            grids.append({"pair": pair, "rows_including_startup": len(selected), "raw_sha256": io.sha(raw), "funding_calendar_sha256": io.sha(fund), "step_seconds": 300})
            if io.sha(raw) != records["raw_5m"]["sha256"] or io.sha(fund) != records["funding_dates"]["sha256"]:
                raise ValueError("Frozen raw/funding calendar input changed")
        with ZipFile(archives[0]) as zipped:
            if zipped.testzip() is not None:
                raise ValueError("Archive integrity failure")
            names = [n for n in zipped.namelist() if n.endswith(".json") and not n.endswith("_config.json")]
            if len(names) != 1:
                raise ValueError("Ambiguous native payload")
            payload = json.loads(zipped.read(names[0]))
        if list(payload["strategy"]) != ["NostalgiaForInfinityX8"]:
            raise ValueError("Wrong actual archive strategy")
        stats = payload["strategy"]["NostalgiaForInfinityX8"]
        if stats.get("pairlist") != protocol["pairs"]:
            raise ValueError("Archive ordered pool differs")
        boundary, fills = [], 0
        for number, trade in enumerate(stats["trades"]):
            if trade["pair"] not in calendars or trade["fee_open"] != 0.0008 or trade["fee_close"] != 0.0008 or trade["leverage"] != 1:
                raise ValueError("Actual trade market/cost/leverage differs")
            boundary.extend(classify_trade(trade, number, calendars[trade["pair"]]))
            fills += len(trade["orders"])
        unknown = [r for r in boundary if r["classification"] == "UNKNOWN"]
        result = {"schema": "individual-8bp-native-clock-applicability.v1", "candidate": CANDIDATE, "status": "CONDITIONAL_NATIVE_SOURCE_CONSTRAINT_SCOPE_WITH_UNKNOWN" if unknown else "CONDITIONAL_NATIVE_SOURCE_CONSTRAINT_SCOPE", "actual_terminal_process_sha256": terminal["process_sha256"], "actual_result_sha256": terminal["result_sha256"], "actual_archive_sha256": io.sha(archives[0]), "actual_effective_config_sha256": io.sha(config_path), "actual_source_sha256": io.sha(source_path), "owner_manifest_sha256": io.sha(protocol["vendor_manifest"]["path"]), "actual_runner_support_bindings": bindings, "freeze_sha256": io.sha(HERE / "freeze.json"), "root_release_sha256": io.sha(HERE / "application_release.json"), "grid": grids, "all_fills_examined": fills, "all_fills_on_5m_grid": True, "boundary_events": boundary, "unknown_boundary_events": len(unknown), "metadata_restored": False, "fifteen_bps_event_inventory_inherited": False, "numeric_cash_nav_funding_recomputed": False, "cash_funding_formulas_or_economic_gates_changed": False, "missing_actual_funding_rate_uncertainties_resolved": False, "historical_rules_or_venue_execution_certified": False, "audit_integration_authorized": False, "orders_enabled": False}
        (destination / "applicability.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    except BaseException as error:
        (destination / "failure.json").write_text(json.dumps({"type": type(error).__name__, "error": str(error), "financial_account_started": False}, indent=2) + "\n")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply-after-terminal", action="store_true", required=True)
    parser.parse_args()
    receipt = apply_after_terminal()
    print(json.dumps({"status": receipt["status"], "fills": receipt["all_fills_examined"], "boundary_events": len(receipt["boundary_events"]), "unknown_events": receipt["unknown_boundary_events"]}))
