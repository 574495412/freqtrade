"""Freeze the native interface policy before running policy diagnostics."""

# ruff: noqa: E501 - frozen human-readable protocol strings

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).parent.resolve()
ROOT = HERE.parents[2]
SOURCE = HERE.with_name("head_shoulders_source_native_20261117_v1")
ENGINE = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(name, value):
    with (HERE / name).open("x") as stream:
        json.dump(value, stream, indent=2, default=str, allow_nan=False)
        stream.write("\n")


def main():
    if shutil.disk_usage(HERE).free < 1024**3:
        raise RuntimeError("Free disk below 1 GiB")
    source_protocol = json.loads((SOURCE / "protocol.json").read_text())
    old_freeze = json.loads((SOURCE / "implementation_freeze.json").read_text())
    for path, expected in old_freeze["files"].items():
        if sha(path) != expected:
            raise ValueError("Frozen v1 input differs: " + path)
    old_manifest = SOURCE / "artifact_manifest.json"
    if sha(old_manifest) != "ed3f314b59ee5e645c0cc1a78febe73ca1c6fc1097c37512fce7c95468fba284":
        raise ValueError("Sealed source artifact manifest differs")
    wide = ROOT / "research/crypto/strategy_comparison_wide_20261105/plan.json"
    wide_plan = json.loads(wide.read_text())
    if wide_plan["pairs"] != source_protocol["ordered_pairs"]:
        raise ValueError("Original ordered pool differs")
    snapshot = wide_plan["market_snapshot"]
    if sha(snapshot["path"]) != snapshot["sha256"]:
        raise ValueError("Frozen public market snapshot differs")
    inputs = list(source_protocol["inputs"])
    auxiliary = []
    for item in inputs:
        if sha(item["path"]) != item["sha256"]:
            raise ValueError("Frozen original hourly input differs")
        prefix = item["pair"].replace("/", "_").replace(":", "_")
        for suffix in ["5m-futures", "1h-mark", "1h-funding_rate"]:
            path = Path(wide_plan["datadir"]) / "futures" / (prefix + "-" + suffix + ".feather")
            auxiliary.append(
                {
                    "pair": item["pair"],
                    "kind": suffix,
                    "path": str(path),
                    "sha256": sha(path),
                    "bytes": path.stat().st_size,
                }
            )
    base = HERE.with_name("hawkes_source_native_20261117_v1")
    config = json.loads((base / "hawkes_before_orders_1000_conservative_attempt3.json").read_text())
    for key in list(config):
        if key.startswith("research_"):
            config.pop(key)
    config.update(
        strategy="HeadShouldersStrategy",
        bot_name="head_shoulders_interface_only",
        max_open_trades=6,
        position_stacking=False,
        timeframe="1h",
        timeframe_detail="5m",
        user_data_dir=str(HERE / "native_probe"),
        strategy_path=str(HERE),
        datadir=wide_plan["datadir"],
        db_url="sqlite://",
        export="none",
        timerange="1735686000-1788220800",
        research_head_shoulders=True,
        research_head_shoulders_source_loader=str(SOURCE / "SourcePatterns.py"),
        research_head_shoulders_history={
            row["pair"]: {"path": row["path"], "sha256": row["sha256"]} for row in inputs
        },
    )
    config["exchange"]["pair_whitelist"] = source_protocol["ordered_pairs"]
    write("native_config.json", config)
    write(
        "social_receipts.json",
        {
            "recorded_at": datetime.now(UTC).isoformat(),
            "verified_by": "parent /root via web tool; child records delegated primary-source receipt",
            "sources": [
                {
                    "url": "https://www.youtube.com/watch?v=6iFqjd5BOHw",
                    "published": "2023-03-21",
                    "title": "Automated Head and Shoulders Chart Pattern in Python | Algorithmic Trading Strategy",
                    "observed_material": "author video metadata and description only",
                    "description_paraphrase": "BTC pattern results varied across scales; the author described an early inverted branch favorably",
                    "video_watched": False,
                    "full_transcript_retrieved": False,
                    "profit_claim_adopted": False,
                },
                {
                    "url": "https://www.youtube.com/watch?v=X31hyMhB-3s",
                    "published": "2023-03-07",
                    "observed_material": "author description of rolling extrema, directional change and PIP algorithms",
                    "video_watched": False,
                    "full_transcript_retrieved": False,
                },
            ],
            "x_author_primary_post_found": False,
            "douyin_executable_original_rules_found": False,
            "secondary_promotional_summary_used_as_rules": False,
            "parameters_changed_from_social_material": False,
            "preserved_parameters": {"order": 6, "early_find": False, "variant": "stop_return"},
        },
    )
    protocol = {
        "registered_at": datetime.now(UTC).isoformat(),
        "scope": "native interface and callback admission only; no financial Backtesting.start",
        "source_manifest": {"path": str(old_manifest), "sha256": sha(old_manifest)},
        "source_protocol": {
            "path": str(SOURCE / "protocol.json"),
            "sha256": sha(SOURCE / "protocol.json"),
        },
        "source_loader": {
            "path": str(SOURCE / "SourcePatterns.py"),
            "sha256": sha(SOURCE / "SourcePatterns.py"),
        },
        "engine_source": str(ENGINE),
        "engine_manifest": {
            "path": str(ENGINE.parent / "FREQTRADE_SOURCE.json"),
            "sha256": sha(ENGINE.parent / "FREQTRADE_SOURCE.json"),
        },
        "engine_commit": "c051e9065c5b677f03817dd1f83cb980af1ed684",
        "ordered_pairs": source_protocol["ordered_pairs"],
        "market_snapshot": snapshot,
        "historically_dated_market_rules_certified": False,
        "native_config_sha256": sha(HERE / "native_config.json"),
        "history_inputs": inputs,
        "native_auxiliary_inputs": auxiliary,
        "data_origin": "2023-08-01T00:00:00Z",
        "native_start": "2024-12-31T23:00:00Z",
        "native_end_exclusive": "2026-09-01T00:00:00Z",
        "source_parameters": source_protocol["source_parameters"],
        "policy": {
            "leverage": 1,
            "max_open_trades": 6,
            "position_stacking": False,
            "entry": "market on first next-1h open; source signal/tag on confirmation bar; native shifts once",
            "late_entry": "reject any timestamp other than exact first entry boundary",
            "reentry": "same source event cannot be reused; an approved entry is consumed",
            "overlap": "ignore event when same pair already has a position; do not replace original trade metadata",
            "same_clock_reverse": "reject events when pair had a position at loop start or closed one at same clock",
            "same_pair_same_bar_dual_event": "reject ambiguity, no direction priority selection",
            "self_terminal": "retain all event IDs; explicitly mask known zero-duration events as flat",
            "event_binding": "trade.enter_tag selects immutable original event stop/target/expiry",
            "source_exit": "strict completed 1h close crossing only, expiry break_i+head_width-1",
            "callback_clock": "only date+1h<=current_time; source exit fills at next native allowed 5m open",
            "native_catastrophe": "-99% hard stop and native liquidation remain separate exit types",
            "roi": {},
            "dca": False,
            "trailing": False,
            "errors": "entry callback returns False on errors; invalid exit binding gets separate hs_policy_error_exit; any callback error invalidates a future financial run",
            "read_only_prehistory": "prepend only frozen dates before native frame, derive exact v1 recognition anew",
            "source_event_parquet_used_as_signal_cache": False,
        },
        "policy_outcomes_observed_before_registration": False,
        "financial_start_allowed": False,
        "financial_results_observed": False,
        "native_financial_accounts_completed": 0,
        "orders_enabled": False,
        "goal_complete": False,
        "resource_limits": {
            "maximum_rss_bytes": 2 * 1024**3,
            "minimum_free_disk_bytes": 1024**3,
            "workers": 1,
        },
    }
    write("protocol.json", protocol)
    files = [
        "register.py",
        "HeadShouldersInputs.py",
        "HeadShouldersStrategy.py",
        "test_callbacks.py",
        "native_probe.py",
        "protocol.json",
        "native_config.json",
        "social_receipts.json",
    ]
    write(
        "implementation_freeze.json",
        {
            "frozen_at": datetime.now(UTC).isoformat(),
            "before_policy_diagnostics": True,
            "files": {str(HERE / name): sha(HERE / name) for name in files},
        },
    )
    print(
        json.dumps(
            {
                "registered": True,
                "protocol_sha256": sha(HERE / "protocol.json"),
                "implementation_freeze_sha256": sha(HERE / "implementation_freeze.json"),
            }
        )
    )


if __name__ == "__main__":
    main()
