"""Seal completed component/input work without claiming an equity backtest."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from bootstrap import boot


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    engine = boot()
    for source in read(HERE / "github_sources.json")["files"]:
        assert sha(source["path"]) == source["sha256"]
    for source in read(HERE / "official_source_receipts.json"):
        if source["status"] == 200:
            assert sha(source["path"]) == source["sha256"]
    admission = read(HERE / "rdability_input_admission.json")
    for path, expected in admission["sources"].items():
        assert sha(path) == expected
    trace = read(HERE / "native_synthetic_trace.json")
    for path, expected in trace["source_hashes"].items():
        assert sha(path) == expected
    for log, phrase in [
        ("tests_v7.log", "33 passed"),
        ("regression_initial.log", "30 passed"),
        ("recency_regression.log", "3 passed"),
        ("ruff_production.log", "All checks passed"),
        ("ruff_research.log", "All checks passed"),
    ]:
        assert phrase in (HERE / log).read_text(), log
    assert admission["original_universe_preserved"] == 514
    assert all(not r["factor_input_admitted"] and r["score"] is None for r in admission["records"])
    files = {
        str(p): sha(p)
        for p in sorted(HERE.rglob("*"))
        if p.is_file()
        and "__pycache__" not in str(p)
        and ".pytest_cache" not in str(p)
        and p.name != "completion_receipt.json"
    }
    extension = ROOT / "freqtrade/equity_wallets.py"
    files[str(extension)] = sha(extension)
    result = dict(
        sealed_utc=datetime.now(UTC).isoformat(),
        component_stage_complete=True,
        research_objective_complete=False,
        native_commit=engine["commit"],
        verified_upstream_files=len(engine["files"]),
        new_checks_passed=33,
        existing_regression_checks_passed=33,
        synthetic_native_fills=4,
        synthetic_snapshot_count=8,
        financial_accounts=0,
        accepted_new_alphas=0,
        validated_new_combinations=0,
        new_registry_definitions=0,
        factor_analysis_calls=0,
        backtest_start_calls=0,
        financial_bench_records=0,
        financial_return=None,
        independent_oos=False,
        all_source_inputs_preserved=514,
        real_orders=False,
        service_changes=False,
        paper_worker_changes=False,
        deployment=False,
        commit=False,
        push=False,
        limitations=[
            "Full EquityBacktesting entry remains closed",
            "Cash restriction tested only at completed native fill boundaries",
            "No certified historical settlement calendar or broker credits",
            "No admitted long-history R&D/sales fiscal-year inputs",
            "Corporate actions, dividends and complete stock execution unfinished",
        ],
        artifacts=files,
    )
    (HERE / "completion_receipt.json").write_text(json.dumps(result, indent=2))
    print(
        f"Sealed {len(files)} artifacts; 66 checks; "
        "0 historical equity accounts; objective incomplete"
    )


if __name__ == "__main__":
    main()
