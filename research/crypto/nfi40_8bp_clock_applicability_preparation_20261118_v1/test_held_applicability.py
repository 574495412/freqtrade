"""Mock-only preparation; filesystem access to actual unfinished8bp output is denied."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from . import verify_applicability as verifier

HERE = Path(__file__).resolve().parent


class ForbiddenActualRead(RuntimeError):
    pass


class MockIO:
    def __init__(self, data):
        self.data = deepcopy(data)
        self.calls = []

    def read_json(self, path):
        key = str(path)
        self.calls.append(("read_json", key))
        if key not in self.data:
            raise FileNotFoundError(key)
        return deepcopy(self.data[key])

    def sha(self, path):
        key = str(path)
        self.calls.append(("sha", key))
        if key not in self.data:
            raise FileNotFoundError(key)
        return hashlib.sha256(json.dumps(self.data[key], sort_keys=True).encode()).hexdigest()


def rejected(call, exception=ValueError):
    try:
        call()
    except exception:
        return
    raise AssertionError("Required rejection did not occur")


def main():
    io = verifier.LocalIO()
    protocol = io.read_json(HERE / "protocol.json")
    frozen = io.read_json(HERE / "freeze.json")
    actual = Path(protocol["expected_actual_output_directory"])
    original_open = Path.open
    blocked = []

    def guarded_open(path, *args, **kwargs):
        if Path(path).is_relative_to(actual):
            blocked.append(str(path))
            raise ForbiddenActualRead("Actual unfinished8bp artifact access is forbidden in fixtures")
        return original_open(path, *args, **kwargs)

    results = []
    with patch.object(Path, "open", guarded_open):
        for path, digest in frozen["files"].items():
            assert not Path(path).is_relative_to(actual)
            assert io.sha(path) == digest, path
        rejected(lambda: io.read_json(actual / "result.json"), ForbiddenActualRead)
        rejected(lambda: io.sha(actual / "native_results/forbidden-fixture.zip"), ForbiddenActualRead)
        results.append("actual_result_and_zip_reads_guarded_before_filesystem")
        # All following actual-path strings are handled exclusively by in-memory IO.
        result_path, process_path = actual / "result.json", actual / "process_receipt.json"
        mock_result = {"candidate": verifier.CANDIDATE, "strategy": "NostalgiaForInfinityX8", "cost_bps_per_side": 8, "orders_enabled": False}
        mock_result_sha = hashlib.sha256(json.dumps(mock_result, sort_keys=True).encode()).hexdigest()
        good_process = {"returncode": 0, "result_sha256": mock_result_sha}
        for name, value in (("receipt_missing", None), ("process_pending", {"returncode": None}), ("process_failed1", {"returncode": 1}), ("resource_abort73", {"returncode": 73}), ("result_hash_missing", {"returncode": 0})):
            data = {} if value is None else {str(process_path): value}
            fake = MockIO(data)
            rejected(lambda: verifier.terminal_identity(fake, actual), verifier.Held)
            assert fake.calls == [("read_json", str(process_path))]
            results.append(name + "_no_result_or_zip_read")
        fake = MockIO({str(process_path): {**good_process, "result_sha256": "0" * 64}, str(result_path): mock_result})
        rejected(lambda: verifier.terminal_identity(fake, actual))
        assert ("read_json", str(result_path)) not in fake.calls
        results.append("mismatched_result_hash_rejected_before_result_parse")
        for name, mutation in (("wrong_candidate", lambda r: r.update(candidate="nfi40_x8_500_cost15")), ("wrong_result_cost", lambda r: r.update(cost_bps_per_side=15)), ("orders_enabled", lambda r: r.update(orders_enabled=True))):
            result = deepcopy(mock_result)
            mutation(result)
            record = {"returncode": 0, "result_sha256": hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()}
            rejected(lambda: verifier.terminal_identity(MockIO({str(process_path): record, str(result_path): result}), actual))
            results.append(name + "_rejected")
        fake = MockIO({str(process_path): good_process, str(result_path): mock_result})
        terminal = verifier.terminal_identity(fake, actual)
        assert fake.calls == [("read_json", str(process_path)), ("sha", str(result_path)), ("read_json", str(result_path)), ("sha", str(process_path))]
        results.append("mock_success_requires_receipt_then_result_hash_then_parse")
        release_path = HERE / "application_release.json"
        freeze_sha = io.sha(HERE / "freeze.json")
        release = {"candidate": verifier.CANDIDATE, "root_reviewed": True, "apply_individual_clock_certificate": True, "freeze_sha256": freeze_sha, "process_receipt_sha256": terminal["process_sha256"], "result_sha256": terminal["result_sha256"], "native_zip_sha256": "1" * 64, "effective_config_sha256": "2" * 64}
        rejected(lambda: verifier.require_release(MockIO({}), release_path, freeze_sha, terminal), verifier.Held)
        results.append("missing_after_terminal_root_release_rejected")
        for name, mutation in (("wrong_freeze", lambda r: r.update(freeze_sha256="0" * 64)), ("wrong_terminal_receipt", lambda r: r.update(process_receipt_sha256="0" * 64)), ("wrong_terminal_result", lambda r: r.update(result_sha256="0" * 64)), ("root_review_missing", lambda r: r.update(root_reviewed=False))):
            wrong = deepcopy(release)
            mutation(wrong)
            rejected(lambda: verifier.require_release(MockIO({str(release_path): wrong}), release_path, freeze_sha, terminal), verifier.Held)
            results.append(name + "_release_rejected")
        assert verifier.require_release(MockIO({str(release_path): release}), release_path, freeze_sha, terminal) == release
        results.append("mock_exact_after_terminal_release_accepted")
        config = deepcopy(protocol["fixture_effective_config"])
        verifier.validate_config(config, protocol["pairs"])
        for name, mutation in (("wrong_fee", lambda c: c.update(fee=0.0015)), ("wrong_capital", lambda c: c.update(dry_run_wallet=1000)), ("detail_enabled", lambda c: c.update(timeframe_detail="1m")), ("entry_timeout_changed", lambda c: c["unfilledtimeout"].update(entry=10)), ("exit_timeout_changed", lambda c: c["unfilledtimeout"].update(exit=10)), ("pool_reordered", lambda c: c["exchange"]["pair_whitelist"].reverse()), ("method_override", lambda c: c.update(ft_check_timed_out="override"))):
            wrong = deepcopy(config)
            mutation(wrong)
            rejected(lambda: verifier.validate_config(wrong, protocol["pairs"]))
            results.append(name + "_config_rejected")
        verifier.validate_hash_bindings("1" * 64, "2" * 64, protocol["required_source_sha256"], release, protocol)
        for name, values in (("wrong_archive", ("0" * 64, "2" * 64, protocol["required_source_sha256"])), ("wrong_effective_config", ("1" * 64, "0" * 64, protocol["required_source_sha256"])), ("wrong_source", ("1" * 64, "2" * 64, "0" * 64))):
            rejected(lambda: verifier.validate_hash_bindings(*values, release, protocol))
            results.append(name + "_hash_rejected")
        F = 1735689600000

        def order(t, entry=True, amount=1.0):
            return {"order_filled_timestamp": t, "ft_is_entry": entry, "amount": amount}

        def trade(orders, reason="exit_fixture"):
            return {"pair": "FIXTURE/USDT:USDT", "is_open": False, "open_timestamp": orders[0]["order_filled_timestamp"], "close_timestamp": orders[-1]["order_filled_timestamp"], "orders": orders, "exit_reason": reason}

        for name, item, role, status in (
            ("ordinary_DCA", trade([order(F - 300000), order(F), order(F + 300000, False, 2)]), "additional_entry", verifier.CONDITIONAL),
            ("ordinary_final", trade([order(F - 300000), order(F, False)]), "ordinary_final_exit", verifier.CONDITIONAL),
            ("initial_entry", trade([order(F), order(F + 300000, False)]), "initial_entry", "UNKNOWN"),
            ("partial_exit", trade([order(F - 300000, True, 2), order(F, False), order(F + 300000, False)]), "partial_exit", "UNKNOWN"),
            ("force_exit", trade([order(F - 300000), order(F, False)], "force_exit"), "force_or_risk_final_exit", "UNKNOWN"),
            ("liquidation", trade([order(F - 300000), order(F, False)], "liquidation"), "force_or_risk_final_exit", "UNKNOWN"),
            ("unrecognized_exit", trade([order(F - 300000), order(F, False)], "unregistered"), "unclassified_final_exit", "UNKNOWN"),
        ):
            rows = verifier.classify_trade(item, 0, {F})
            assert len(rows) == 1 and rows[0]["event_role"] == role and rows[0]["classification"] == status
            assert rows[0]["created_ms"] is None and rows[0]["raw_metadata_status"] == "UNKNOWN"
            assert rows[0]["conditional_inferred_creation_ms"] == (F if status == verifier.CONDITIONAL else None)
            results.append(name + "_role_scope")
        good = trade([order(F - 300000), order(F, False)])
        for name, mutation in (("off_grid", lambda t: t["orders"][-1].update(order_filled_timestamp=F + 1)), ("wrong_order_role_type", lambda t: t["orders"][-1].update(ft_is_entry="exit")), ("initial_not_entry", lambda t: t["orders"][0].update(ft_is_entry=False)), ("wrong_final_clock", lambda t: t.update(close_timestamp=F + 300000))):
            wrong = deepcopy(good)
            mutation(wrong)
            rejected(lambda: verifier.classify_trade(wrong, 0, {F}))
            results.append(name + "_export_rejected")
        metadata = deepcopy(good)
        metadata["orders"][-1]["order_timestamp"] = F
        assert verifier.classify_trade(metadata, 0, {F})[0]["classification"] == "UNKNOWN"
        results.append("different_creation_metadata_schema_not_auto_admitted")
    assert len(blocked) == 2
    value = {"passed": True, "case_count": len(results), "cases": results, "all_terminal_release_config_archive_source_inputs_mocked": True, "actual8bp_output_file_handles_opened": 0, "actual8bp_result_or_zip_parsed": False, "actual8bp_pending_receipt_inspected": False, "explicit_blocked_actual_path_attempts": blocked, "account_function_or_native_engine_called": False, "root_application_release_created": False, "future_applicability_claim": "UNKNOWN_NOT_EXECUTED"}
    (HERE / "mock_validation.json").write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"passed": True, "mock_cases": len(results), "actual8bp_file_reads": 0, "future_application": "HELD"}))


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        import traceback
        (HERE / "mock_failure.json").write_text(json.dumps({"type": type(error).__name__, "error": str(error), "traceback": traceback.format_exc()}, indent=2) + "\n")
        raise
