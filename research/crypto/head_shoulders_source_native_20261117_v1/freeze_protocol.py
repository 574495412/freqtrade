"""Freeze source, input hashes and non-economic diagnostics before recognition."""

import hashlib
import json
import platform
import shutil
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow

from .SourcePatterns import load_author


STUDY = Path(__file__).parent.resolve()
ROOT = STUDY.parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name, data):
    with (STUDY / name).open("x") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main():
    if shutil.disk_usage(STUDY).free < 1024**3:
        raise RuntimeError("Free disk below 1 GiB")
    plan_path = ROOT / "research/crypto/strategy_comparison_wide_20261105/plan.json"
    plan = json.loads(plan_path.read_text())
    if len(plan["pairs"]) != 40 or len(set(plan["pairs"])) != 40:
        raise ValueError("Original ordered 40-pair pool differs")
    inputs = []
    candles = ROOT / "user_data/crypto_quant/nfi40_data_v002/candles/futures"
    for pair in plan["pairs"]:
        name = pair.replace("/", "_").replace(":", "_") + "-1h-futures.feather"
        path = candles / name
        inputs.append({"pair": pair, "path": str(path), "sha256": sha(path)})
    source_receipts = json.loads((STUDY / "source_receipts.json").read_text())
    write(
        "protocol.json",
        {
            "frozen_at": datetime.now(UTC).isoformat(),
            "source_repository": source_receipts["repository"],
            "source_commit": source_receipts["commit"],
            "license": source_receipts["license"],
            "source_receipts_sha256": sha(STUDY / "source_receipts.json"),
            "original_pool_plan": {"path": str(plan_path), "sha256": sha(plan_path)},
            "ordered_pairs": plan["pairs"],
            "inputs": inputs,
            "data_origin": "2023-08-01T00:00:00Z",
            "data_end": "2026-08-31T23:00:00Z",
            "expected_continuous_rows_per_pair": 27048,
            "source_parameters": {
                "price": "natural_log_close",
                "order": 6,
                "early_find": False,
                "source_rule_variant": "stop_return",
                "r_squared_filter": None,
                "hold_return_selected": False,
                "parameter_search": False,
                "stop": "right shoulder log price",
                "target": "neckline at confirmation plus/minus head height",
                "crossing": "strict > or <; touches do not exit",
                "source_expiry_index": "break_i + head_width - 1",
                "source_return_observation_begins": "break_i, including zero-duration events",
            },
            "source_event_selection": "all normal and inverse patterns, no post-return filter",
            "event_clock": "break_i candle close, never the earlier pivot timestamp",
            "prefix_cutoffs_exclusive": ["2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z"],
            "terminal_prefix_excluded_hours": 240,
            "future_shock": "multiply all future OHLC prices by 4, starting at each cutoff",
            "prefix_equality": "all pattern fields and derived event fields exact",
            "market_data_return_helper_evaluation": False,
            "synthetic_return_helper_fixtures_only": True,
            "unknown_tail_policy": "retain events; observation coverage does not imply known exit",
            "self_terminal_policy": "retain count and event; do not infer a native filled trade",
            "overlap_and_capital_policy": "UNREGISTERED; no account signals",
            "diagnostics_observed_before_freeze": False,
            "economic_results_observed_before_freeze": False,
            "native_accounts_completed": 0,
            "financial_performance_proven": False,
            "orders_enabled": False,
            "deployment_enabled": False,
            "resource_limits": {
                "maximum_rss_bytes": 2 * 1024**3,
                "minimum_free_disk_bytes": 1024**3,
                "workers": 1,
            },
            "environment": {
                "python": platform.python_version(),
                "numpy": np.__version__,
                "pandas": pd.__version__,
                "pyarrow": pyarrow.__version__,
                "author_full_environment_reproduced": False,
            },
        },
    )
    _, nodes = load_author()
    write("source_ast_manifest.json", {"selection_only_no_ast_rewriting": True, "nodes": nodes})
    frozen = [
        "protocol.json",
        "source_receipts.json",
        "tree_api_receipt.json",
        "source_ast_manifest.json",
        "SourcePatterns.py",
        "validate_source.py",
        "freeze_protocol.py",
        "fetch_sources.py",
        "source/head_shoulders.py",
        "source/rolling_window.py",
        "source/LICENSE",
        "source/README.md",
    ]
    write(
        "implementation_freeze.json",
        {
            "frozen_at": datetime.now(UTC).isoformat(),
            "before_source_pattern_diagnostics": True,
            "before_economic_results": True,
            "files": {str(STUDY / name): sha(STUDY / name) for name in frozen},
        },
    )
    print(
        json.dumps(
            {
                "frozen": True,
                "pairs": len(inputs),
                "protocol_sha256": sha(STUDY / "protocol.json"),
                "implementation_freeze_sha256": sha(STUDY / "implementation_freeze.json"),
            }
        )
    )


if __name__ == "__main__":
    main()
