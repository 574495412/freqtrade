"""Verified bridge to archived native NFI runners; CLI never executes a backtest.

The parent P1 orchestrator owns the new registered run manifest, budget and output
path. This module retains the old hourly-anchor scope, not the new 23:55 endpoint.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

try:
    from . import guard
except ImportError:
    import guard


def _read(name):
    return json.loads((guard.HERE / name).read_text())


def dataset_record(dataset_id):
    return next(d for d in _read("data_manifest.json")["datasets"] if d["id"] == dataset_id)


def _absolute_records(value):
    if isinstance(value, dict):
        return {key: (str(guard.local_path(item)) if key == "path" else _absolute_records(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_absolute_records(item) for item in value]
    return value


def load_prepared_plan(dataset_id):
    """Return a fresh legacy-compatible plan using only this repository's paths."""
    plan = _absolute_records(deepcopy(_read("prepared_plans.json")[dataset_id]))
    plan["engine_source"] = str(guard.ROOT)
    plan["datadir"] = str(guard.local_path(plan["datadir"]))
    return plan


def verify_prepared_plan(plan, candidate):
    expected = load_prepared_plan(plan["dataset_id"])
    if plan != expected or candidate != expected["candidates"][0]:
        raise ValueError("Prepared native parity contract changed; use a newly registered P1 adapter")
    guard.verify_environment(_read("source_environment.json"))
    guard.verify_dataset(dataset_record(plan["dataset_id"]), payload=True)
    for name in ("storage_source", "observer_source", "market_snapshot", "environment", "runner", "guard_source"):
        guard.checked(plan[name])
    for item in (candidate["config"], *candidate["source_files"].values(), *plan["marks"].values()):
        guard.checked(item)
    if plan["research_support"] or plan["RD1_applied"] or plan["risk_profile"] != "native_baseline":
        raise ValueError("Native baseline must not load legacy research namespace or RD1")
    return guard.ROOT


def load_legacy_runner(dataset_id):
    """Return the pinned runner with only source/path verification adapted.

    No financial function is called here. Caller must register and hash its run
    before invoking module.run(plan_path,candidate_name,cost,unique_output_path).
    Native strategy, matching, wallet, funding and observer implementation remain
    the archived versions. This is engineering parity, never economic acceptance.
    """
    plan = load_prepared_plan(dataset_id)
    verify_prepared_plan(plan, plan["candidates"][0])
    guard.install_runtime_guards()
    path = guard.checked(plan["runner"])
    spec = importlib.util.spec_from_file_location(f"rd_archived_runner_{dataset_id}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The old verifier assumes the engine directory is an extracted vendor tree.
    # Only that assumption is replaced; no native engine or simulation methods.
    module.verify = verify_prepared_plan
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, choices=["spot_L", "spot_S", "futures_L", "futures_S"])
    parser.add_argument("--write-plan", help="Fresh JSON path under this repository's user_data/crypto_quant")
    args = parser.parse_args()
    plan = load_prepared_plan(args.dataset)
    verify_prepared_plan(plan, plan["candidates"][0])
    if args.write_plan:
        destination = guard.local_path(args.write_plan)
        if not destination.is_relative_to(guard.ROOT / "user_data/crypto_quant") or destination.suffix != ".json":
            raise ValueError("Prepared plans belong in isolated user_data/crypto_quant JSON output")
        if destination.exists():
            raise ValueError("Never overwrite a prepared plan")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x") as stream:
            json.dump(plan, stream, indent=2)
            stream.write("\n")
    print(json.dumps({"dataset": args.dataset, "preflight": "passed", "candidate": plan["candidates"][0]["name"],
                      "runner": str(guard.checked(plan["runner"]).relative_to(guard.ROOT)),
                      "scope": plan["scope"], "financial_runs": 0, "RD1_applied": False,
                      "execution_requires": "P1 frozen run manifest and orchestrator; this CLI has no execute action"}))


if __name__ == "__main__":
    main()
