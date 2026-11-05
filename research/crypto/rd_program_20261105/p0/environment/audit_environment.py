"""Bounded, no-network P0 audit. Writes only small manifests beside this script."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import sys

import guard

HERE, ROOT = guard.HERE, guard.ROOT
PARENTS = {
    "spot_L": "research/crypto/spot_baseline_20261105_v003/plan-long.json",
    "spot_S": "research/crypto/spot_baseline_20261105_v003/plan-short.json",
    "futures_L": "research/crypto/strategy_comparison_wide_20261105/plan.json",
    "futures_S": "research/crypto/strategy_comparison_wide_20261105/plan-post-source.json",
}
RUNNERS = {
    "spot": "research/crypto/spot_baseline_20261105_v003/run_spot_comparison.py",
    "futures": "research/crypto/strategy_comparison_wide_20261105/run_wide_comparison.py",
}


def relative(value):
    return str(guard.local_path(value).relative_to(ROOT))


def record(path):
    path = guard.local_path(str(path))
    return {"path": str(path.relative_to(ROOT)), "sha256": guard.sha(path)}


def portable(value):
    if isinstance(value, dict):
        return {key: (relative(item) if key == "path" else portable(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [portable(item) for item in value]
    return value


def write(name, value):
    destination = HERE / name
    if destination.exists():
        raise ValueError(f"Do not overwrite an audit result: {name}")
    destination.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def date_audit(path, start, end):
    import pandas as pd
    # Only the date column is read; this is not a fresh OHLC/value audit.
    dates = pd.to_datetime(pd.read_feather(path, columns=["date"])["date"], utc=True)
    if dates.isna().any():
        raise ValueError(f"Invalid date column: {path.name}")
    match = re.search(r"-(5m|15m|1h|4h|1d)(?:-(futures|mark|funding_rate))?\.feather$", path.name)
    if not match:
        raise ValueError(f"Unsupported data filename: {path.name}")
    timeframe, kind = match.groups()
    kind = kind or "spot"
    result = {"file": path.name, "timeframe": timeframe, "kind": kind,
              "rows": len(dates), "first": dates.iloc[0].isoformat(), "last": dates.iloc[-1].isoformat(),
              "duplicates": int(dates.duplicated().sum()), "ordered": bool(dates.is_monotonic_increasing),
              "pre_evaluation_rows": int((dates < start).sum())}
    if kind != "funding_rate":
        freq = {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}[timeframe]
        expected = pd.date_range(start, end, freq=freq, inclusive="left")
        selected = pd.DatetimeIndex(dates[(dates >= start) & (dates < end)])
        result.update(evaluation_rows=len(selected), expected_evaluation_rows=len(expected),
                      missing_evaluation_rows=len(expected.difference(selected)),
                      extra_evaluation_rows=len(selected.difference(expected)))
    else:
        result.update(evaluation_events=int(((dates >= start) & (dates < end)).sum()),
                      event_grid="irregular actual events; no inferred fixed interval")
    return result


def main():
    import pandas as pd
    guard.install_runtime_guards()
    source_check = guard.verify_environment()
    now = datetime.now(timezone.utc).isoformat()
    old_environment = guard.read_json("research/crypto/strategy_comparison_20261105/environment.json")
    names = sorted(set(old_environment["dependencies"]) | {"psutil", "pydantic", "jsonschema", "requests", "aiohttp"})
    env = {"schema": "freqtrade.rd.source-environment.v1", "observed_at_utc": now,
           "root_resolution": "repository root relative to this script; no legacy execution import path",
           "python": sys.version, "python_path": ".venv-paper/bin/python", "platform": platform.platform(),
           "dependencies": {name: importlib.metadata.version(name) for name in names},
           "upstream_manifest": "deploy/upstream-source.json",
           "upstream_manifest_sha256": guard.sha(ROOT / "deploy/upstream-source.json"),
           "source_check": source_check, "legacy_environment": record("research/crypto/strategy_comparison_20261105/environment.json"),
           "runtime_guards": ["reject FREQTRADE environment overrides", "reject legacy project sys.path", "deny cz and freqtrade.research imports", "deny outbound sockets", "disable bytecode writes"],
           "live_runtime_modified": False, "financial_backtests_run": 0}
    datasets, files, prepared = [], {}, {}
    for dataset_id, parent_path in PARENTS.items():
        market = dataset_id.split("_")[0]
        parent = guard.read_json(parent_path)
        source_candidate = next(c for c in parent["candidates"] if c["name"] in ("nfi-x8-spot", "nfi-x8-1x"))
        receipt_path = guard.checked(parent["data_receipts"])
        receipt = json.loads(receipt_path.read_text())
        directory = guard.local_path(parent["datadir"])
        runner = record(RUNNERS[market])
        if runner["sha256"] != parent["runner_sha256"]:
            raise ValueError("Archived runner differs from parent registration")
        supports = {key: portable(parent[key]) for key in ("storage_source", "observer_source", "market_snapshot", "environment")}
        supports.update(runner=runner, strategy=portable(source_candidate["source_files"]["NostalgiaForInfinityX8.py"]),
                        native_config=portable(source_candidate["config"]))
        total_bytes = sum((directory / name).stat().st_size for name in receipt)
        dataset = {"id": dataset_id, "market": market, "window_id": dataset_id.split("_")[1],
                   "parent_plan": record(parent_path), "datadir": relative(parent["datadir"]),
                   "data_receipt": portable(parent["data_receipts"]), "file_count": len(receipt),
                   "total_bytes": total_bytes, "start": parent["start"], "end_exclusive": parent["end"],
                   "old_valuation_endpoint": parent["comparison_end"], "new_valuation_endpoint": (pd.Timestamp(parent["end"]) - pd.Timedelta(minutes=5)).isoformat(),
                   "support_records": supports, "source_payload_verification": guard.verify_dataset({
                       "parent_plan": record(parent_path), "data_receipt": portable(parent["data_receipts"]),
                       "datadir": relative(parent["datadir"]), "file_count": len(receipt), "total_bytes": total_bytes,
                       "support_records": supports, "id": dataset_id}),
                   "legacy_runtime_dependency": "engine_source and engine_manifest referred to the other project; new adapter uses repository pinned core only"}
        rows = [date_audit(directory / name, pd.Timestamp(parent["start"]), pd.Timestamp(parent["end"])) for name in receipt]
        files[dataset_id] = rows
        failures = [r for r in rows if r["duplicates"] or not r["ordered"] or r.get("missing_evaluation_rows", 0) or r.get("extra_evaluation_rows", 0)]
        dataset["date_audit"] = {"file_count": len(rows), "failures": failures,
                                 "all_registered_evaluation_grids_complete": not failures,
                                 "kind_timeframe_file_counts": dict(Counter(f"{r['kind']}/{r['timeframe']}" for r in rows)),
                                 "scope": "all receipt payload hashes and all date columns; OHLC/volume values not independently re-audited"}
        dataset["five_minute_mark_available"] = bool(market == "spot" or any(r["kind"] == "mark" and r["timeframe"] == "5m" for r in rows))
        dataset["valuation_evidence"] = "spot 5m OPEN reference, not executable bid" if market == "spot" else "only hourly mark; 5m trade OPEN is conditional reference, not certified mark"
        dataset["blocks"] = ["X8 exit-alert causality unresolved", "native same-time event trace not yet reconciled", "new 23:55 endpoint not evaluated"]
        if market == "futures":
            dataset["blocks"].append("5m mark absent; prior simplified orders lack creation time")
        datasets.append(dataset)
        # Restrict the in-memory adapter to fields actually used by the archived runner.
        keys = ("engine_commit", "start", "end", "comparison_end", "pairs", "initial_equity_usdt", "allowed_leverage", "total_cost_bps_per_side", "periods", "memory_guard_bytes", "wall_guard_seconds")
        plan = {key: parent[key] for key in keys}
        plan.update(schema="freqtrade.rd.standalone-parity-adapter.v1", dataset_id=dataset_id,
                    parent_plan=record(parent_path), engine_source=".",
                    engine_manifest=record("deploy/upstream-source.json"),
                    datadir=dataset["datadir"], data_receipts=dataset["data_receipt"],
                    storage_source=supports["storage_source"], observer_source=supports["observer_source"],
                    market_snapshot=supports["market_snapshot"], environment=supports["environment"],
                    marks=portable(parent["marks"]), candidates=[portable({
                        key: value for key, value in source_candidate.items() if key != "original_source_files"
                    })],
                    research_support={}, runner=runner, runner_sha256=runner["sha256"],
                    risk_profile="native_baseline", RD1_applied=False,
                    scope="legacy hourly-anchor engineering parity only; not new P1 endpoint or economic qualification",
                    guard_source=record(HERE / "guard.py"), financial_runs_started=0)
        prepared[dataset_id] = plan
    write("source_environment.json", env)
    write("data_manifest.json", {"schema": "freqtrade.rd.data-readiness.v1", "observed_at_utc": now,
                                "datasets": datasets, "data_downloaded": False, "financial_runs": 0,
                                "historical_availability_certified": False, "economic_acceptance": False})
    write("data_file_inventory.json", {"observed_at_utc": now, "date_columns_only": True, "datasets": files})
    write("prepared_plans.json", prepared)
    print(json.dumps({"official_files_verified": source_check["official_package_files_verified"],
                      "datasets": [{"id": d["id"], "files": d["file_count"], "bytes": d["total_bytes"], "date_failures": len(d["date_audit"]["failures"])} for d in datasets],
                      "new_financial_runs": 0, "manifests_written": 4}))


if __name__ == "__main__":
    main()
