"""Independent signed-state receipts and actual native execution-clock audit.

Only the hash-pinned independent NumPy function is extracted from test_inputs.
No candidate, author callbacks, account engine or trading simulation is imported.
The audit checks exported orders; account cash/funding remains a separate audit.
"""

# ruff: noqa: S101
import argparse
import ast
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
PLAN_SHA256 = "56556161ef3d664cc8a802eb6d5e070f47473ccb956db8b130e0cbd480401c92"
INDEPENDENT_SOURCE_SHA256 = "996807d30511b55b662ef894dfe9cbf0105da8571a9f68c0c27c33513cc52447"
SOURCE_EXIT = "source_hawkes_state_change"
OTHER_EXIT_CATEGORIES = {
    "force_exit": "terminal_force_exit",
    "stop_loss": "stoploss",
    "trailing_stop_loss": "stoploss",
    "stoploss_on_exchange": "stoploss",
    "liquidation": "liquidation",
    "emergency_exit": "emergency_exit",
}
PRICE_FIELDS = ["open", "high", "low", "close", "volume"]
FLAG_FIELDS = ["enter_long", "enter_short", "exit_long", "exit_short"]


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def independent_calculator():
    """Compile one independent arithmetic definition, without executing imports."""
    path = HERE / "test_inputs.py"
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == INDEPENDENT_SOURCE_SHA256
    tree = ast.parse(raw.decode(), filename=str(path))
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "independent_features"
    ]
    assert len(selected) == 1
    assert not any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in ast.walk(selected[0]))
    namespace = {"np": np, "pd": pd}
    exec(  # noqa: S102 - one hash-pinned independent arithmetic function only
        compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace
    )
    return namespace["independent_features"]


def expected_inputs(plan):
    operation = independent_calculator()
    data_record = plan["data_receipts"]
    assert sha(data_record["path"]) == data_record["sha256"]
    receipts = read(data_record["path"])
    frames, proofs = {}, []
    for pair in plan["pairs"]:
        name = pair.replace("/", "_").replace(":", "_") + "-1h-futures.feather"
        relative = "futures/" + name
        path = Path(plan["datadir"]) / relative
        digest = sha(path)
        assert digest == receipts[relative]
        frame = pd.read_feather(path)
        dates = pd.DatetimeIndex(frame.date).as_unit("ns")
        assert str(dates.tz) == "UTC" and dates[0] == pd.Timestamp("2023-08-01T00:00Z")
        assert dates.equals(pd.date_range(dates[0], dates[-1], freq="h"))
        assert np.isfinite(frame[PRICE_FIELDS].to_numpy(dtype=float)).all()
        signal = operation(frame).signal
        assert signal.isin([-1, 0, 1]).all()
        calculated = frame.copy(deep=True)
        calculated["audit_state"] = signal.to_numpy()
        calculated["enter_long"] = signal.eq(1).to_numpy(dtype=int)
        calculated["enter_short"] = signal.eq(-1).to_numpy(dtype=int)
        calculated["exit_long"] = signal.ne(1).to_numpy(dtype=int)
        calculated["exit_short"] = signal.ne(-1).to_numpy(dtype=int)
        frames[pair] = calculated.set_index("date")
        proofs.append({
            "pair": pair, "input": str(path), "input_sha256": digest,
            "rows": len(frame), "origin": str(dates[0]),
            "signed_state_counts": {str(int(k)): int(v) for k, v in signal.value_counts().items()},
            "independent_state_sha256": hashlib.sha256(
                dates.asi8.tobytes() + signal.to_numpy(dtype="<f8").tobytes()
            ).hexdigest(),
        })
    return frames, proofs


def expected_signal_receipt(frame, strategy, plan):
    selected = frame.loc[
        (frame.index >= pd.Timestamp(plan["start"]))
        & (frame.index < pd.Timestamp(plan["end"]))
    ]
    expected = pd.date_range(plan["start"], plan["comparison_end"], freq="h")
    assert selected.index.as_unit("ns").equals(expected)
    fields = PRICE_FIELDS + FLAG_FIELDS
    values = selected[fields].to_numpy(dtype="<f8", copy=True)
    dates = selected.index.as_unit("ns").asi8
    return {
        "strategy": strategy, "rows": len(selected), "fields": fields,
        "sha256": hashlib.sha256(
            dates.tobytes() + np.ascontiguousarray(values).tobytes()
        ).hexdigest(),
    }


def completed_signal(frames, pair, timestamp):
    fill = pd.Timestamp(timestamp, unit="ms", tz="UTC")
    assert fill == fill.floor("5min"), (pair, fill, "off-detail-grid fill")
    opening = fill.floor("h") - pd.Timedelta(hours=1)
    assert opening + pd.Timedelta(hours=1) <= fill
    return int(frames[pair].loc[opening, "audit_state"]), fill, opening


def native_trades(directory, strategy):
    archives = list((directory / "native_results").glob("*.zip"))
    assert len(archives) == 1, (directory, archives)
    with ZipFile(archives[0]) as archive:
        matches = []
        for name in archive.namelist():
            if name.endswith(".json") and not name.endswith("_config.json"):
                value = json.loads(archive.read(name))
                if isinstance(value, dict) and strategy in value.get("strategy", {}):
                    matches.append(value["strategy"][strategy])
        assert len(matches) == 1
    return matches[0]["trades"], archives[0]


def audit_one(candidate, plan, frames):
    directory = Path(plan["output_root"]) / (
        candidate["name"] + f"_{candidate['cost_bps']}bps"
    )
    process = read(directory / "process_receipt.json")
    assert process["returncode"] == 0
    assert sha(directory / "result.json") == process["result_sha256"]
    for name, record in candidate["source_files"].items():
        assert sha(record["path"]) == record["sha256"]
        assert sha(directory / "strategies" / name) == record["sha256"]
    receipt_path = directory / "native_signal_receipts.json"
    actual_receipts = read(receipt_path)
    expected_receipts = {
        candidate["strategy"] + "|" + pair:
            expected_signal_receipt(frames[pair], candidate["strategy"], plan)
        for pair in candidate["pairs"]
    }
    assert actual_receipts == expected_receipts, (candidate["name"], "native signal SHA mismatch")
    trades, archive = native_trades(directory, candidate["strategy"])
    exits, categories, directions = Counter(), Counter(), Counter()
    signal_exit_changes = Counter()
    order_proofs = []
    lower, upper = pd.Timestamp(plan["start"]), pd.Timestamp(plan["end"])
    for number, trade in enumerate(trades):
        pair = trade["pair"]
        assert pair in candidate["pairs"] and trade["leverage"] == 1
        assert trade["enter_tag"] == "source_hawkes_state"
        orders = trade["orders"]
        assert len(orders) == 2 and orders[0]["ft_is_entry"] and not orders[1]["ft_is_entry"]
        entry, exit_ = orders
        assert entry["order_filled_timestamp"] == trade["open_timestamp"]
        assert exit_["order_filled_timestamp"] == trade["close_timestamp"]
        assert abs(entry["amount"] - exit_["amount"]) < 1e-10
        entered, opened, entry_bar = completed_signal(frames, pair, entry["order_filled_timestamp"])
        exited, closed, exit_bar = completed_signal(frames, pair, exit_["order_filled_timestamp"])
        assert lower <= opened <= closed < upper
        direction = -1 if trade["is_short"] else 1
        assert entered == direction, (candidate["name"], number, pair, opened, entered, direction)
        reason = trade["exit_reason"]
        if reason == SOURCE_EXIT:
            assert exited != direction, (candidate["name"], number, pair, closed, exited, direction)
            category = "source_state_exit"
            signal_exit_changes["flat" if exited == 0 else "reverse"] += 1
        else:
            assert reason in OTHER_EXIT_CATEGORIES, (candidate["name"], number, reason)
            category = OTHER_EXIT_CATEGORIES[reason]
        exits[reason] += 1
        categories[category] += 1
        directions["short" if trade["is_short"] else "long"] += 1
        order_proofs.append({
            "trade_index": number, "pair": pair, "direction": direction,
            "entry_ms": trade["open_timestamp"], "entry_signal_bar": str(entry_bar),
            "entry_prior_completed_state": entered,
            "exit_ms": trade["close_timestamp"], "exit_signal_bar": str(exit_bar),
            "exit_prior_completed_state": exited,
            "exit_reason": reason, "exit_category": category,
        })
    log = HERE / (candidate["name"] + "_attempt3_execution.log")
    log_text = log.read_text()
    for marker in ["Strategy caused the following exception:", "Unexpected error "]:
        assert marker not in log_text, (candidate["name"], marker)
    return {
        "account": candidate["name"], "native_trades": len(trades),
        "native_archive_sha256": sha(archive), "execution_log_sha256": sha(log),
        "native_signal_receipts_sha256": sha(receipt_path),
        "all_pair_signal_receipts_equal_independent_arithmetic": True,
        "all_entry_directions_match_completed_hour": True,
        "source_exit_tags_match_completed_hour_state_change": True,
        "trade_directions": dict(directions), "exit_reasons": dict(exits),
        "exit_categories": dict(categories), "source_exit_states": dict(signal_exit_changes),
        "non_source_exits_not_relabelled": True,
        "risk_trigger_conditions_independently_certified": False,
        "orders": order_proofs,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "executed_policy_audit_attempt3.json")
    args = parser.parse_args()
    assert not args.output.exists(), "Preserve previous audit result"
    plan_path = HERE / "plan_attempt3.json"
    assert sha(plan_path) == PLAN_SHA256
    plan = read(plan_path)
    # Do not start a partial audit or infer terminal state from a controller snapshot.
    for candidate in plan["candidates"]:
        directory = Path(plan["output_root"]) / (
            candidate["name"] + f"_{candidate['cost_bps']}bps"
        )
        assert (directory / "process_receipt.json").is_file(), (
            "Actual native terminal receipt is not available", candidate["name"]
        )
    frames, inputs = expected_inputs(plan)
    accounts = [audit_one(candidate, plan, frames) for candidate in plan["candidates"]]
    result = {
        "passed": True, "verified_at": datetime.now(UTC).isoformat(),
        "plan_sha256": PLAN_SHA256, "audit_source_sha256": sha(__file__),
        "independent_function_file_sha256": INDEPENDENT_SOURCE_SHA256,
        "inputs": inputs, "accounts": accounts,
        "native_accounts_checked": len(accounts),
        "native_trades_checked": sum(row["native_trades"] for row in accounts),
        "source_candidate_or_engine_imported": False,
        "cash_funding_profitability_or_live_execution_proven": False,
        "goal_complete": False,
    }
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "passed": True, "native_accounts_checked": len(accounts),
        "native_trades_checked": result["native_trades_checked"], "output": str(args.output),
    }), flush=True)


if __name__ == "__main__":
    main()
