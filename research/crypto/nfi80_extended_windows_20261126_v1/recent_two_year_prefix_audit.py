"""Check the 2025 overlap of the unchanged annual and continuous native runs."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt, save

from .accounts import write_once
from .inputs import HERE
from .low_disk_run import BLOCK_RESEARCH
from .recent_two_year_run import GATE, RUNS


ANNUAL = {12: "fixed_boost_12", 14: "boost_14"}
STEP = pd.Timedelta(minutes=5)
BOUNDARY = pd.Timestamp("2026-01-01", tz="UTC")
MARKER = b" - NostalgiaForInfinityX8 - INFO - "


def annual_folder(slots):
    return BLOCK_RESEARCH / f"annual_{ANNUAL[slots]}_15bp"


def continuous_folder(slots):
    return RUNS / f"full_{slots}_15bp"


def inputs():
    gate_receipt = receipt(GATE)
    admission = read(checked(gate_receipt))
    assert admission["strategy_inputs_bound_to_native_engine"]
    manifest = read(checked(admission["manifest"]))
    schedule = read(checked(manifest["schedule"]))
    old_pairs = {}
    for slots in ANNUAL:
        prior = annual_folder(slots)
        assert read(prior / "completion.json")["returncode"] == 0
        assert read(prior / "account_reconciled.json")["five_minute_nav_reconciliation"]["passed"]
        annual_plan = receipt(prior / "account/registered_plan.json")
        recent_plan = admission["plans"][f"{slots}_15bp"]
        old, new = (read(checked(row)) for row in (annual_plan, recent_plan))
        for field in ("start", "engine_commit", "engine_manifest", "observer_source", "market_snapshot",
                      "initial_equity_usdt", "allowed_leverage", "observation_frequency", "cost_model", "pairs"):
            assert old[field] == new[field], field
        old_case, = old["candidates"]
        new_case, = new["candidates"]
        assert old_case["strategy"] == new_case["strategy"]
        assert old_case["max_open_trades"] == new_case["max_open_trades"] == slots
        assert {key: item["sha256"] for key, item in old_case["source_files"].items()} == {
            key: item["sha256"] for key, item in new_case["source_files"].items()}
        old_cfg, new_cfg = (read(checked(row["config"])) for row in (old_case, new_case))
        assert {k for k in set(old_cfg) | set(new_cfg) if old_cfg.get(k) != new_cfg.get(k)} == {"pairlists", "bot_name"}
        for raw, plan in ((old_cfg, old), (new_cfg, new)):
            listed, = raw["pairlists"]
            assert listed == {"method": "HistoricalVolumePairList", "schedule": plan["historical_schedule"]["path"],
                              "sha256": plan["historical_schedule"]["sha256"]}
        original_calendar = read(checked(old["historical_schedule"]))["days"]
        assert len(original_calendar) == 365
        assert all(schedule["days"][day] == row for day, row in original_calendar.items())
        leaves = read(checked(old["data_receipts"]))
        assert set(leaves) == set(manifest["files"])
        earliest_from_2025 = 0
        for name, digest in leaves.items():
            chain = manifest["files"][name]
            shard, = [row for row in chain if row["source_year"] == "2025"]
            assert shard["sha256"] == digest and Path(shard["path"]) == Path(old["datadir"]) / name
            first = chain[0] == shard
            earliest_from_2025 += first
            if first:
                assert shard["start_inclusive"] is None
            else:
                assert pd.Timestamp(shard["start_inclusive"]) == pd.Timestamp(old["start"])
            assert pd.Timestamp(shard["stop_exclusive"]) == pd.Timestamp(old["end"])
        old_pairs[str(slots)] = {"annual_plan": annual_plan, "continuous_plan": recent_plan,
                                 "exact_calendar_days": len(original_calendar), "identical_tape_receipts": len(leaves),
                                 "feeds_whose_earliest_shard_is_the_2025_tape": earliest_from_2025}
    return write_once(HERE / "recent_two_year_annual_input_parity.json", {
        "preflight": gate_receipt, "cases": old_pairs, "source": receipt(Path(__file__)),
        "observation_scope": "2025 annual market streams and declared engine/account contract before financial comparison",
        "orders_enabled": False,
    })


def snapshot_log(path):
    sample = path.read_bytes()
    complete = sample[:sample.rfind(b"\n") + 1]
    indicator, sep, remainder = complete.partition(b"Backtesting with data from ")
    assert indicator and sep and remainder
    events = [line.split(MARKER, 1)[1] for line in remainder.splitlines() if MARKER in line]
    return events, {"path": str(path), "observed_bytes": len(complete), "observed_prefix_sha256": sha256(complete).hexdigest()}


def live_snapshot():
    prior = annual_folder(12)
    current = continuous_folder(12)
    assert not (current / "completion.json").exists()
    checkpoint = read(current / "account/replay-clock.json")
    assert pd.Timestamp(checkpoint["clock"]) < BOUNDARY
    reference, prior_bytes = snapshot_log(prior / "native.log")
    replay, replay_bytes = snapshot_log(current / "native.log")
    assert len(replay) <= len(reference) and replay
    differing = next((i for i, (a, b) in enumerate(zip(reference, replay)) if a != b), None)
    record = {"captured_at_utc": datetime.now(timezone.utc).isoformat(), "checkpoint": checkpoint,
              "native_strategy_messages": len(replay), "exact_strategy_message_prefix": differing is None,
              "first_different_index": differing, "annual_log": prior_bytes, "active_log": replay_bytes,
              "last_strategy_message": replay[-1].decode(errors="replace"),
              "observation_scope": "Native strategy log messages during the active replay, before output account reconciliation"}
    path = HERE / "recent_two_year_live_prefix_receipt.json"
    if path.exists():
        earlier = read(path)
        assert earlier["exact_strategy_message_prefix"] and earlier["native_strategy_messages"] <= len(replay)
    save(path, record)
    print(record["native_strategy_messages"], record["exact_strategy_message_prefix"], checkpoint["clock"], flush=True)


def load_completed(folder):
    assert read(folder / "completion.json")["returncode"] == 0
    proof = read(folder / "account_reconciled.json")
    assert proof["five_minute_nav_reconciliation"]["passed"]
    reports = {Path(ref["path"]).suffix: ref for ref in proof["sources"]}
    nav = pd.read_parquet(checked(reports[".parquet"])).set_index("date")
    trades = stats_from(checked(reports[".zip"]))["trades"]
    return nav, trades, reports


def fill_prefix(trades, cutoff):
    ms = cutoff.value // 1_000_000
    return sorted((row["order_filled_timestamp"], trade["pair"], trade["is_short"], row["ft_is_entry"],
                   row["ft_order_side"], row["safe_price"], row["amount"], row.get("ft_order_tag") or "")
                  for trade in trades for row in trade["orders"] if row["order_filled_timestamp"] < ms)


def account_prefix(slots):
    annual, old_trades, old_refs = load_completed(annual_folder(slots))
    continuous, new_trades, new_refs = load_completed(continuous_folder(slots))
    assert len(annual) == 105120 and annual.index[-1] == BOUNDARY - STEP
    assert len(continuous) == 199872 and continuous.index[0] == annual.index[0] and BOUNDARY in continuous.index
    cutoff = annual.index[-1] - STEP
    old_nav, new_nav = (series.loc[:cutoff] for series in (annual, continuous))
    assert old_nav.index.equals(new_nav.index) and old_nav.columns.equals(new_nav.columns)
    exact_nav = old_nav.equals(new_nav)
    left, right = (fill_prefix(book, cutoff) for book in (old_trades, new_trades))
    exact_fills = left == right
    previous_forced = {(row["pair"], row["is_short"], row["open_timestamp"])
                       for row in old_trades if row["exit_reason"] == "force_exit"}
    boundary_ms = BOUNDARY.value // 1_000_000
    new_held = {(row["pair"], row["is_short"], row["open_timestamp"])
                for row in new_trades if row["open_timestamp"] < boundary_ms <= row["close_timestamp"]}
    return {"common_mark_cutoff": cutoff.isoformat(), "checked_marks": len(old_nav),
            "max_marked_equity_difference_usdt": float((old_nav.marked_equity - new_nav.marked_equity).abs().max()),
            "exact_marks": exact_nav, "exact_fills": exact_fills, "fill_counts": {"annual": len(left), "continuous": len(right)},
            "annual_forced_positions": sorted(previous_forced), "continuous_positions_at_new_year": sorted(new_held),
            "continuous_carry_matched": previous_forced == new_held,
            "annual_receipts": old_refs, "continuous_receipts": new_refs}


def completed():
    rows = {str(slots): account_prefix(slots) for slots in ANNUAL
            if (continuous_folder(slots) / "account_reconciled.json").exists()}
    save(HERE / "recent_two_year_completed_prefix_audit.json", {
        "cases": rows, "complete_cases": len(rows), "orders_enabled": False,
    })
    for name, data in rows.items():
        print(name, data["exact_marks"], data["exact_fills"], data["continuous_carry_matched"], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("live", "completed"))
    options = parser.parse_args()
    inputs()
    if options.mode == "live":
        live_snapshot()
    else:
        completed()


if __name__ == "__main__":
    main()
