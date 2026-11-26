"""Recheck actual artifacts, native owners and completed check results."""

import hashlib
import importlib.metadata
import json
import re
import subprocess
from pathlib import Path

from bootstrap import boot

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    manifest = boot()
    from freqtrade.exchange.exchange_utils import amount_to_precision, price_to_precision
    import inspect

    protocol = json.loads((HERE / "protocol.json").read_text())
    assert sha(protocol["parent_plan"]) == protocol["parent_plan_sha256"]
    for path, expected in protocol["implementation"].items():
        assert sha(path) == expected, path
    assert sha(HERE / "source_receipts.json") == protocol["source_receipts_sha256"]
    receipts = json.loads((HERE / "source_receipts.json").read_text())
    for record in receipts:
        assert record["status"] == 200
        assert sha(record["path"]) == record["sha256"]
        assert Path(record["path"]).stat().st_size == record["bytes"]
        if record["path"].endswith(".pdf"):
            assert Path(record["path"]).read_bytes().startswith(b"%PDF")
    audit = json.loads((HERE / "input_precision_audit.json").read_text())
    assert audit["protocol_sha256"] == sha(HERE / "protocol.json")
    assert len(audit["records"]) == protocol["universe_count"] == 514
    assert audit["checked_ohlc_cells"] == 10 * 408 * 4 == 16320
    assert audit["exact_off_grid_cells"] == 0
    assert audit["status_counts"] == {
        "NOT_PROBED_NO_HISTORICAL_INSTRUMENT_CONTRACT": 503,
        "CONDITIONAL_QUOTE_GRID_DIAGNOSTIC": 10,
        "UNKNOWN_ETP_TABLE_UNSUPPORTED": 1,
    }
    assert audit["financial_accounts"] == 0 and audit["return_metrics"] is None
    trace = json.loads((HERE / "native_synthetic_trace.json").read_text())
    assert trace["test_source_sha256"] == sha(HERE / "test_execution.py")
    assert trace["native_fills"] == 3 and len(trace["snapshots"]) == 5
    assert trace["cash_and_quantity_reconciled"]
    assert not trace["funded_backtesting_start_called"]
    checks = {}
    for filename, count in [
        ("tests_final.log", 46),
        ("settlement_regression.log", 33),
        ("history_provider_regression.log", 30),
    ]:
        value = (HERE / filename).read_text()
        assert re.search(rf"\b{count} passed\b", value), filename
        assert not re.search(r"\b(FAILED|ERROR|failed|error)\b", value), filename
        checks[filename] = dict(passed=count, sha256=sha(HERE / filename))
    result = subprocess.run(
        [
            "/Users/chrise/GitHub/Vibe-Trading/.venv/bin/ruff",
            "check",
            "freqtrade/data/equity_execution.py",
            "freqtrade/optimize/equity_execution.py",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    verification = dict(
        native_commit=manifest["commit"],
        native_verified_files=len(manifest["files"]),
        native_amount_owner=inspect.getfile(amount_to_precision),
        native_price_owner=inspect.getfile(price_to_precision),
        local_extension=True,
        checks=checks,
        unique_checks_passed=sum(r["passed"] for r in checks.values()),
        production_ruff_passed=True,
        source_captures_verified=len(receipts),
        source_clock_or_master_certification=False,
        environment={
            k: importlib.metadata.version(k) for k in ["pandas", "numpy", "ccxt", "pytest"]
        },
        financial_accounts=0,
        new_validated_alphas=0,
        return_metrics=None,
        objective_achieved=False,
        independent_oos=False,
        orders=False,
        deployment=False,
        initial_failure="tests_initial.log: decorator syntax prevented collection; "
        "fixed before execution; no failed financial runs relabeled.",
    )
    (HERE / "verification.json").write_text(json.dumps(verification, indent=2) + "\n")
    print(json.dumps(verification, indent=2))


if __name__ == "__main__":
    main()
