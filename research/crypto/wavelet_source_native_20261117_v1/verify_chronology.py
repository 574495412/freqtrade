"""Independent row-index reconstruction and pinned-source verification; no probe import."""

# ruff: noqa: S101
# This file is a verification entry point, not production execution logic.
import ast
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
if not __debug__:
    raise RuntimeError("Verification requires assertions")


def reference(n):
    """Independent integer clock contract from the published974/973/6 slices."""
    windows = []
    last = 974
    while last < n:
        windows.append((last - 974, last))
        last += 973
    windows.append((n - 974, n))
    latest = [-1] * n
    for start, stop in windows:
        for row in range(start, stop):
            latest[row] = stop - 1
    invalid = sum(end > row for row, end in enumerate(latest))
    after_warmup = sum(latest[row] > row for row in range(128, n))
    return windows, latest, invalid, after_warmup


def main():
    diagnostic = json.loads((HERE / "chronology_audit.json").read_text())
    windows, latest, invalid, warm = reference(3000)
    assert invalid == diagnostic["full"]["rows_with_future_training_labels"]
    assert warm == diagnostic["full"]["rows_with_future_training_labels_after128warmup"]
    assert windows == [
        (r["first_output_id"], r["last_output_id"] + 1) for r in diagnostic["full"]["assignments"]
    ]
    for (start, stop), fit in zip(windows, diagnostic["full"]["fits"], strict=True):
        assert fit["first_feature_id"] == start
        assert fit["last_feature_id"] == stop - 7
        assert fit["latest_label_id"] == stop - 1
        assert fit["training_rows"] == 968
    _, short, _, _ = reference(2800)
    changed = sum(latest[i] != short[i] for i in range(128, 2800))
    assert (
        changed == diagnostic["appended200rows_change_past_training_assignment_afterwarmup"] == 974
    )
    receipt = diagnostic["data_receipt"]
    assert hashlib.sha256(Path(receipt["path"]).read_bytes()).hexdigest() == receipt["sha256"]
    sources = json.loads((HERE / "source_receipts.json").read_text())
    for row in sources:
        data = Path(row["local_path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == row["sha256"]
        assert (
            hashlib.sha1(
                b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False
            ).hexdigest()
            == row["git_blob"]
        )
    original = HERE / "sources/rmallarapu-bc_nateemma/binanceus/DWT_Predict.py"
    tree = ast.parse(original.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DWT_Predict")
    method = next(
        n
        for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name == "add_jumping_predictions"
    )
    alias = any(
        isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Name)
        and n.value.id == "close_data"
        and any(isinstance(t, ast.Name) and t.id == "pred_array" for t in n.targets)
        for n in ast.walk(method)
    )
    assert alias
    final_backfill = any(
        isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Name)
        and n.value.id == "preds"
        and any(
            isinstance(t, ast.Subscript)
            and isinstance(t.value, ast.Name)
            and t.value.id == "pred_array"
            for t in n.targets
        )
        for n in ast.walk(method)
    )
    assert final_backfill
    result = {
        "passed": True,
        "source_files_git_and_sha_verified": len(sources),
        "independent_integer_windows_match": True,
        "source_array_alias_assignment_confirmed": True,
        "source_historical_backfill_assignment_confirmed": True,
        "actual_xgboost_predictions_or_pnl_verified": False,
        "legacy_source_admission": False,
        "current_upstream_certified": False,
        "new_native_wallets": 0,
        "new_financial_runs": 0,
        "source_training_timestamp_violation_rows_afterwarmup": warm,
        "shorter_prefix_historical_model_assignment_changes": changed,
        "chronology_audit_sha256": hashlib.sha256(
            (HERE / "chronology_audit.json").read_bytes()
        ).hexdigest(),
    }
    (HERE / "independent_verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
