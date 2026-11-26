"""Independent resolution of the actual current source defaults and dispatch owner."""

# ruff: noqa: S101
# This is an audit entry point; assertions cannot be disabled.
import ast
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
if not __debug__:
    raise RuntimeError("Verification requires assertions")


def node(path, name):
    return next(
        n
        for n in ast.parse(path.read_text()).body
        if isinstance(n, ast.ClassDef) and n.name == name
    )


def methods(cls):
    return {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}


def constant(cls, name):
    for n in cls.body:
        if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in n.targets
        ):
            return ast.literal_eval(n.value)
    raise KeyError(name)


def main():
    records = json.loads((HERE / "source_receipts.json").read_text())
    records += json.loads((HERE / "upstream_followup_receipts.json").read_text())
    for record in records:
        raw = Path(record["local_path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == record["sha256"]
        assert (
            hashlib.sha1(
                b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False
            ).hexdigest()
            == record["git_blob"]
        )
    folder = HERE / "sources/nateemma_strategies/TSPredict"
    parent = node(folder / "TSPredict.py", "TSPredict")
    middle = node(folder / "TS_Wavelet.py", "TS_Wavelet")
    child = node(folder / "TS_Wavelet_DWT.py", "TS_Wavelet_DWT")
    assert constant(parent, "static_model") is False
    assert constant(parent, "training_mode") is False
    assert constant(middle, "use_rolling") is False
    assert "add_predictions" in methods(parent)
    assert "add_predictions" not in methods(middle)
    assert "add_predictions" not in methods(child)
    assert "add_jumping_predictions" not in methods(child)
    actual = methods(middle)["add_jumping_predictions"]
    assert [a.arg for a in actual.args.args] == ["self", "dataframe"]
    returns = [n for n in ast.walk(actual) if isinstance(n, ast.Return)]
    assert len(returns) == 1
    call = returns[0].value
    assert isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
    assert isinstance(call.func.value, ast.Name) and call.func.value.id == "self"
    assert call.func.attr == "add_rolling_predictions"
    assert (
        len(call.args) == 1
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "dataframe"
    )
    dynamic = json.loads((HERE / "upstream_dispatch_audit_v2.json").read_text())
    assert dynamic["passed_source_dispatch_probe"]
    assert dynamic["previous_default_signature_mismatch_claim_withdrawn"]
    for path, digest in dynamic["inputs"].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
    correction = json.loads((HERE / "dispatch_probe_correction.json").read_text())
    for path, digest in correction["original_probe_inputs"].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
    clocks = json.loads((HERE / "upstream_clock_audit.json").read_text())
    expected_labels = {}
    for key, size in [("full", 6000), ("prefix", 5800)]:
        expected = []
        for start in range(1000, size, 2500):
            stop = min(start + 2500, size)
            expected.append(
                {
                    "first_output_clock": start,
                    "last_output_clock": stop - 1,
                    "latest_label_source_clock": start - 1,
                    "rows": stop - start,
                }
            )
        assert expected == clocks[key]["prediction_windows"]
        assert clocks[key]["scaling_windows_checked"] == size - 1000
        expected_labels[key] = {
            i: row["latest_label_source_clock"]
            for row in expected
            for i in range(row["first_output_clock"], row["last_output_clock"] + 1)
        }
    assert all(
        expected_labels["full"][i] == value for i, value in expected_labels["prefix"].items()
    )
    assert clocks["appended200rows_changed_prior_training_model_clock"] == 0
    for path, digest in clocks["source_inputs"].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
    result = {
        "passed": True,
        "pinned_source_git_blobs_verified": len(records),
        "current_default_dispatcher": "TSPredict.add_predictions",
        "current_jump_owner": "TS_Wavelet.add_jumping_predictions",
        "current_effective_route": "TS_Wavelet.add_rolling_predictions",
        "default_arity_error_claim_valid": False,
        "previous_probe_error_preserved": True,
        "actual_models_or_native_initialization_certified": False,
        "current_cpu_cold_scaler_rolling_row_clock_reference_matches": True,
        "new_financial_runs": 0,
    }
    (HERE / "upstream_independent_verification_v2.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
