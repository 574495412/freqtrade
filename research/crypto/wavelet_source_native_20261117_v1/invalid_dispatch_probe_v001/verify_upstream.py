"""Independent source defaults, override ownership, signature and Git-blob checks."""

# ruff: noqa: S101
# Source audit assertions cannot be disabled.
import ast
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
if not __debug__:
    raise RuntimeError("Verification requires assertions")


def class_node(path, name):
    tree = ast.parse(path.read_text())
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)


def value(cls, name):
    matches = []
    for node in cls.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            matches.append(ast.literal_eval(node.value))
    assert matches
    return matches[-1]


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
    parent = class_node(folder / "TSPredict.py", "TSPredict")
    middle = class_node(folder / "TS_Wavelet.py", "TS_Wavelet")
    child = class_node(folder / "TS_Wavelet_DWT.py", "TS_Wavelet_DWT")
    assert value(parent, "static_model") is False
    assert value(parent, "training_mode") is False
    assert value(middle, "use_rolling") is False
    for cls in [middle, child]:
        assert not any(
            isinstance(n, ast.FunctionDef)
            and n.name in ["add_predictions", "add_jumping_predictions"]
            for n in cls.body
        )
    predictor = next(
        n for n in middle.body if isinstance(n, ast.FunctionDef) and n.name == "predict_data"
    )
    assert [a.arg for a in predictor.args.args] == ["self", "X"]
    jumping = next(
        n
        for n in parent.body
        if isinstance(n, ast.FunctionDef) and n.name == "add_jumping_predictions"
    )
    calls = [
        n
        for n in ast.walk(jumping)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "self"
        and n.func.attr == "predict_data"
    ]
    assert calls and all(len(c.args) == 2 and not c.keywords for c in calls)
    dynamic = json.loads((HERE / "upstream_dispatch_audit.json").read_text())
    assert dynamic["independent_signature_binding_rejected"]
    for path, digest in dynamic["inputs"].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
    result = {
        "passed": True,
        "pinned_git_blobs_verified": len(records),
        "current_default_nonstatic_nonrolling": True,
        "dispatcher_is_inherited_from_pinned_TSPredict": True,
        "parent_call_arguments": 2,
        "subclass_bound_arguments": 1,
        "no_numeric_model_or_native_account_executed": True,
        "native_constructor_or_other_configurations_certified": False,
    }
    (HERE / "upstream_independent_verification.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
