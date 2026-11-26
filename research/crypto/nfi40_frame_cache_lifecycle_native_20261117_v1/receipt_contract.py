"""Strict future receipt comparison, not a financial or matching simulator."""

REQUIRED_EVENTS = {"entry", "position_add", "partial_exit", "full_exit"}
REQUIRED_CALLBACKS = {"confirm_trade_entry", "custom_exit", "adjust_trade_position", "order_filled"}
EQUAL_FIELDS = (
    "contract",
    "native_signal_records",
    "dp_read_records",
    "callback_records",
    "order_lifecycle_records",
    "hourly_wallet_records",
    "native_trade_records",
    "state_records",
    "cache_clear_records",
    "cleanup_records",
)


def compare_receipts(baseline, candidate):
    for receipt in (baseline, candidate):
        if receipt.get("producer_kind") != "ACTUAL_OFFICIAL_BACKTESTING_LIFECYCLE":
            raise ValueError("Detached callbacks or sliced frames are not lifecycle proof")
        if receipt.get("terminal_exit_code") != 0 or receipt.get("callback_errors"):
            raise ValueError("Native lifecycle did not complete cleanly")
        observed = {row["kind"] for row in receipt["order_lifecycle_records"] if row["filled"]}
        if missing := REQUIRED_EVENTS - observed:
            raise ValueError(
                "Missing actual native lifecycle coverage: " + ",".join(sorted(missing))
            )
        callbacks = {row["callback"] for row in receipt["callback_records"]}
        if not REQUIRED_CALLBACKS <= callbacks:
            raise ValueError("Actual NFI callback coverage is incomplete")
        if not receipt["dp_read_records"]:
            raise ValueError("No native analyzed-frame callback read was observed")
        clear_phases = {row["phase"] for row in receipt["cache_clear_records"]}
        if not {"native_reset", "post_cleanup_verification"} <= clear_phases:
            raise ValueError("Native clear/reset/teardown coverage is incomplete")
        if not receipt["cleanup_records"] or not receipt["native_trade_records"]:
            raise ValueError("Native terminal trade/cleanup evidence is absent")
        if receipt["contract"].get("synthetic"):
            raise ValueError("Mechanical fixtures cannot certify unchanged real NFI replay")
    if baseline.get("cache_variant") != "legacy_zstd_lru4":
        raise ValueError("Wrong baseline")
    if candidate.get("cache_variant") != "analyzed_blocks4096_lru32":
        raise ValueError("Wrong candidate")
    for field in EQUAL_FIELDS:
        if baseline[field] != candidate[field]:
            raise ValueError("Native parity differs: " + field)
    return {
        "passed": True,
        "scope": "Exact observed fixed-window lifecycle parity only",
        "required_actual_event_kinds": sorted(REQUIRED_EVENTS),
        "full_window_financial_parity_proven": False,
        "execution_replacement_admitted": False,
    }
