"""Synthetic gate/comparator unit tests only; zero native matching or wallets."""

import copy

from .admission import release_contract
from .receipt_contract import EQUAL_FIELDS, REQUIRED_CALLBACKS, REQUIRED_EVENTS, compare_receipts


def rejects(operation):
    try:
        operation()
    except ValueError:
        return
    raise AssertionError("Invalid synthetic gate was accepted")


def run():
    good = dict(
        plan_sha256="plan",
        producer_freeze_sha256="producer",
        root_reviewed=True,
        technical_native_start_authorized=True,
        existing_controller_replacement_authorized=False,
    )
    release_contract(good, "plan", "producer")
    for field in good:
        altered = dict(good)
        altered.pop(field)
        rejects(lambda altered=altered: release_contract(altered, "plan", "producer"))
    baseline = {field: [] for field in EQUAL_FIELDS}
    baseline.update(
        producer_kind="ACTUAL_OFFICIAL_BACKTESTING_LIFECYCLE",
        terminal_exit_code=0,
        callback_errors=[],
        cache_variant="legacy_zstd_lru4",
        contract={"synthetic": False},
    )
    baseline["dp_read_records"] = [{"synthetic_test_marker": True}]
    baseline["callback_records"] = [{"callback": name} for name in sorted(REQUIRED_CALLBACKS)]
    baseline["order_lifecycle_records"] = [
        {"kind": name, "filled": True} for name in sorted(REQUIRED_EVENTS)
    ]
    baseline["cache_clear_records"] = [
        {"phase": p} for p in ["native_reset", "post_cleanup_verification"]
    ]
    baseline["cleanup_records"] = baseline["native_trade_records"] = [
        {"synthetic_test_marker": True}
    ]
    other = copy.deepcopy(baseline)
    other["cache_variant"] = "analyzed_blocks4096_lru32"
    assert compare_receipts(baseline, other)["passed"]
    for event in REQUIRED_EVENTS:
        altered = copy.deepcopy(other)
        altered["order_lifecycle_records"] = [
            r for r in altered["order_lifecycle_records"] if r["kind"] != event
        ]
        rejects(lambda altered=altered: compare_receipts(baseline, altered))
    for field in EQUAL_FIELDS:
        altered = copy.deepcopy(other)
        if field == "contract":
            altered[field]["source_hash"] = "changed"
        else:
            altered[field].append({"unexpected": True})
        try:
            compare_receipts(baseline, altered)
        except (ValueError, KeyError):
            pass
        else:
            raise AssertionError("Differing field passed: " + field)
    for mode in ["synthetic", "detached", "callback_error", "exit_failure"]:
        altered = copy.deepcopy(other)
        if mode == "synthetic":
            altered["contract"]["synthetic"] = True
        elif mode == "detached":
            altered["producer_kind"] = "DETACHED_CALLBACK"
        elif mode == "callback_error":
            altered["callback_errors"] = ["synthetic failure"]
        else:
            altered["terminal_exit_code"] = 1
        rejects(lambda altered=altered: compare_receipts(baseline, altered))
    return {
        "passed": True,
        "kind": "SYNTHETIC_GATE_AND_COMPARATOR_UNIT_TESTS",
        "native_lifecycle_exercised": False,
        "native_accounts_started": 0,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run()))
