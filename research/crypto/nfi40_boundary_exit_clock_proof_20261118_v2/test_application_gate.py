"""Classification-gate negative controls; no financial code import."""

from copy import deepcopy
import json
from pathlib import Path

from .application_gate import HERE, read, validate_semantics


def main():
    protocol = read(HERE / "protocol.json")
    directory = Path(protocol["effective_config"]).parent
    entry_dir = Path(protocol["entry_proof_directory"])
    baseline = {
        "account": protocol["actual_account"], "config": read(directory / "effective_config.json"),
        "process": read(directory / "process_receipt.json"), "result": read(directory / "result.json"),
        "grid": read(HERE / "input_grid_validation.json"), "entry": read(entry_dir / "validation.json"),
        "exit_": read(HERE / "validation.json"),
    }
    assert len(validate_semantics(**baseline)) == 5
    cases = [("8bp_not_inherited", lambda d: d.update(account="nfi40_x8_500_cost8")),
             ("nonterminal_process", lambda d: d["process"].update(returncode=None)),
             ("failed_process", lambda d: d["process"].update(returncode=73)),
             ("detail_changed", lambda d: d["config"].update(timeframe_detail="1m")),
             ("exit_timeout_changed", lambda d: d["config"]["unfilledtimeout"].update(exit=10)),
             ("missing_grid", lambda d: d["grid"].update(passed=False)),
             ("fill_off_grid", lambda d: d["grid"].update(all_fills_on_5m_grid=False)),
             ("incomplete_exit_proof", lambda d: d["exit_"].update(status="UNKNOWN")),
             ("wrong_window", lambda d: d["config"].update(timerange="unknown")),
             ("fabricated_creation_metadata", lambda d: d["exit_"]["actual_boundary_final_exits"][0].update(creation_exported=True))]
    for name, mutation in cases:
        altered = deepcopy(baseline)
        mutation(altered)
        try:
            validate_semantics(**altered)
        except ValueError:
            continue
        raise AssertionError(name)
    result = {"passed": True, "positive_actual_scope": 1, "negative_controls": [n for n, _ in cases], "numeric_audit_imported": False}
    (HERE / "application_gate_validation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"passed": True, "positive_scopes": 1, "negative_controls": len(cases)}))


if __name__ == "__main__":
    main()
