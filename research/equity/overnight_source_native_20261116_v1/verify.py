"""Read-only source/producer/input reconciliation after the completed run."""

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from bootstrap import HERE, save, sha


def main():
    freeze = json.loads((HERE / "implementation_freeze.json").read_text())
    execution = json.loads((HERE / "execution_receipt.json").read_text())
    completed = datetime.fromisoformat(execution[-1]["ended_at"]).timestamp()
    doc_drift = []
    for path, digest in freeze.items():
        if sha(path) == digest:
            continue
        # Another workspace study updated only the two owner documents after
        # all children and the driver's final strict freeze check completed.
        # Keep the original freeze and failure receipt; never waive code drift.
        assert path in {
            "/Users/chrise/GitHub/Vibe-Trading/agent/src/skills/factor-research/SKILL.md",
            "/Users/chrise/GitHub/Vibe-Trading/agent/src/skills/strategy-dev-manager/SKILL.md",
        }, path
        assert Path(path).stat().st_mtime > completed, path
        doc_drift.append({"path": path, "initial_sha256": digest, "current_sha256": sha(path)})
    hashes = json.loads((HERE / "input_hashes.json").read_text())
    for path, digest in hashes.items():
        assert sha(path) == digest, path
    parent = HERE.with_name("mechanism_batch_native_20261116_v1")
    seal = json.loads((parent / "artifact_manifest.json").read_text())
    for path, digest in seal["files"].items():
        assert sha(parent / path) == digest, path
    native = json.loads((HERE / "native_result.json").read_text())
    ref = json.loads((HERE / "input_reference.json").read_text())
    inputs = json.loads(Path(ref["inputs"]).read_text())
    assert sha(ref["inputs"]) == ref["inputs_sha256"]
    original = {r["pair"]: r for r in inputs["records"]}
    assert len(native["records"]) == len(original) == 514
    cells = 0
    for cohort in native["cohorts"]:
        fields = {
            field: pd.read_parquet(HERE / "native" / cohort["market"] / f"{field}.parquet")
            for field in ["open", "high", "low", "close", "volume"]
        }
        for pair in fields["close"].columns:
            source = pd.read_parquet(original[pair]["staged"])
            assert pd.DatetimeIndex(source.date).equals(fields["close"].index)
            for field, frame in fields.items():
                np.testing.assert_array_equal(source[field].to_numpy(), frame[pair].to_numpy())
                cells += len(source)
    assert len(execution) == 3 and all(r["returncode"] == 0 for r in execution)
    result = json.loads((HERE / "predictive_result.json").read_text())
    audit = json.loads((HERE / "independent_audit.json").read_text())
    assert audit["status"] == "passed" and audit["diagnostic_calls"] == 18
    assert result["actual_factor_analysis_calls"] == len(result["results"]) == 18
    assert not any(g["numeric_gate"] for g in result["gates"])
    save(
        HERE / "verification.json",
        {
            "passed": True,
            "frozen_paths": len(freeze),
            "unchanged_frozen_paths": len(freeze) - len(doc_drift),
            "post_run_owning_document_changes": doc_drift,
            "scope": "Code/input/result verification; owner docs changed after completed execution",
            "unchanged_input_paths": len(hashes),
            "unchanged_parent_paths": len(seal["files"]),
            "actual_native_quote_cells_bound_to_staged_inputs": cells,
            "native_source_files_verified": native["owner"]["verified_files"],
            "original_security_queue": 514,
            "admitted": 486,
            "rejected_preserved": 28,
            "actual_factor_analysis_calls": 18,
            "unique_tests": 11,
            "completed_child_processes": 3,
            "independent_audit": audit,
            "historical_accounts": 0,
            "portfolio_accounts": 0,
            "bench_records": 0,
            "new_isolated_research_definitions": 1,
            "accepted_independent_alphas": 0,
            "new_financial_account_returns": None,
            "author_performance_reproduced": False,
            "independent_oos": False,
            "production_eligible": False,
            "orders_enabled": False,
            "goal_complete": False,
        },
    )
    print("Verified", len(freeze), "producer paths;", cells, "quote cells;18FA;0accounts")


if __name__ == "__main__":
    main()
