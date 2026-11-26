"""All original 40 pairs: source event chronology and geometric admission only."""

from __future__ import annotations

import hashlib
import json
import resource
import shutil
import sys
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd

from .SourcePatterns import extract_events, load_author, validate_frame


STUDY = Path(__file__).parent.resolve()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def guard():
    free = shutil.disk_usage(STUDY).free
    require(free >= 1024**3, "Free disk below 1 GiB")
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_bytes = peak if sys.platform == "darwin" else peak * 1024
    require(rss_bytes <= 2 * 1024**3, "Peak RSS above 2 GiB")
    return {"free_disk_bytes": free, "peak_rss_bytes": rss_bytes}


def geometric_constraints(frame, events):
    prices = np.log(frame.close.to_numpy())
    checks = 0
    for item in events.to_dict("records"):
        indexes = [
            item[key]
            for key in [
                "start_i",
                "l_shoulder",
                "l_armpit",
                "head",
                "r_armpit",
                "r_shoulder",
                "break_i",
            ]
        ]
        require(all(a < b for a, b in pairwise(indexes)), "Pattern index order")
        start, left, left_armpit, head, right_armpit, right, confirmation = indexes
        inverted = item["inverted"]
        require(item["break_p"] == prices[confirmation], "Confirmation price differs")
        for key in ["l_shoulder", "r_shoulder", "l_armpit", "r_armpit", "head"]:
            require(
                item[key + "_p"] == prices[item[key]], "Pattern quoted historical price differs"
            )
        for pivot, kind in [(left, 1), (left_armpit, -1), (head, 1), (right_armpit, -1)]:
            require(pivot + 6 <= confirmation, "Extreme used before its confirmation")
            window = prices[pivot - 6 : pivot + 7]
            is_peak = (kind == 1) != inverted
            expected = np.max(window) if is_peak else np.min(window)
            require(prices[pivot] == expected, "Independent confirmed extreme mismatch")
        choose = np.argmin if inverted else np.argmax
        require(
            right == right_armpit + 1 + choose(prices[right_armpit + 1 : confirmation]),
            "Right shoulder differs",
        )
        if inverted:
            require(prices[head] < min(prices[left], prices[right]), "Inverse head geometry")
            require(
                prices[left] <= (prices[right] + prices[right_armpit]) / 2
                and prices[right] <= (prices[left] + prices[left_armpit]) / 2,
                "Inverse shoulder balance",
            )
        else:
            require(prices[head] > max(prices[left], prices[right]), "Head geometry")
            require(
                prices[left] >= (prices[right] + prices[right_armpit]) / 2
                and prices[right] >= (prices[left] + prices[left_armpit]) / 2,
                "Shoulder balance",
            )
        require(
            right - head <= 2.5 * (head - left) and head - left <= 2.5 * (right - head),
            "Time symmetry",
        )
        slope = (prices[right_armpit] - prices[left_armpit]) / (right_armpit - left_armpit)

        def neck(index, anchor=left_armpit, gradient=slope):
            return prices[anchor] + (index - anchor) * gradient

        height = neck(head) - prices[head] if inverted else prices[head] - neck(head)
        candidates = range(left - 1, max(-1, left - (right_armpit - left_armpit)), -1)
        beginning = next(
            (
                index
                for index in candidates
                if (prices[index] > neck(index) if inverted else prices[index] < neck(index))
            ),
            None,
        )
        require(start == beginning, "Independent first neckline crossing differs")
        for actual, expected in [
            (item["neck_slope"], slope),
            (item["neck_start"], neck(start)),
            (item["neck_end"], neck(confirmation)),
            (item["head_height"], height),
        ]:
            require(
                np.isclose(actual, expected, rtol=1e-12, atol=1e-12),
                "Independent neckline mismatch",
            )
        require(item["head_width"] == right_armpit - left_armpit, "Independent head width mismatch")
        require(
            prices[confirmation] >= neck(confirmation)
            if inverted
            else prices[confirmation] <= neck(confirmation),
            "Pattern unconfirmed at recorded break",
        )
        target = neck(confirmation) + height if inverted else neck(confirmation) - height
        require(
            np.isclose(item["source_target_log_price"], target, rtol=1e-12, atol=1e-12),
            "Target mismatch",
        )
        require(item["source_stop_log_price"] == prices[right], "Right-shoulder stop mismatch")
        at_target = prices[confirmation] > target if inverted else prices[confirmation] < target
        at_stop = (
            prices[confirmation] < prices[right]
            if inverted
            else prices[confirmation] > prices[right]
        )
        require(
            item["self_terminal_at_confirmation"] == bool(at_target or at_stop),
            "Independent self-terminal recognition mismatch",
        )
        require(
            item["source_expiry_i"] == confirmation + right_armpit - left_armpit - 1,
            "Source expiry mismatch",
        )
        require(
            item["earliest_signal_available"]
            == frame.date.iloc[confirmation] + pd.Timedelta(hours=1),
            "Backfilled signal clock",
        )
        checks += 1
    return checks


def source_exit_fixtures(author):
    """Only synthetic fixtures call the author's forward-looking return helper."""
    cases = []
    for inverted in [False, True]:
        pattern = author.HSPattern(inverted=inverted)
        pattern.break_i = 2
        pattern.break_p = 10.0
        pattern.r_shoulder_p = 8.0 if inverted else 12.0
        pattern.neck_end = 10.0
        pattern.head_height = 2.0
        pattern.head_width = 4
        for label, values, expected in [
            (
                "target_equal_does_not_exit",
                [
                    10,
                    10,
                    10,
                    12 if inverted else 8,
                    11 if inverted else 9,
                    10.5 if inverted else 9.5,
                ],
                0.5,
            ),
            (
                "stop_equal_does_not_exit",
                [
                    10,
                    10,
                    10,
                    8 if inverted else 12,
                    9 if inverted else 11,
                    10.5 if inverted else 9.5,
                ],
                0.5,
            ),
            ("strict_target_cross", [10, 10, 10, 13 if inverted else 7, 8, 8], 3.0),
            ("strict_stop_cross", [10, 10, 10, 7 if inverted else 13, 12, 12], -3.0),
            (
                "expiry_uses_width_minus_one",
                [10, 10, 10, 10, 10, 10.5 if inverted else 9.5, 15],
                0.5,
            ),
        ]:
            observed = author.get_pattern_return(np.array(values, dtype=float), pattern)
            require(observed == expected, f"Source exit fixture failed: {inverted}/{label}")
            cases.append({"inverted": inverted, "case": label, "passed": True})
        unknown = author.get_pattern_return(np.array([10, 10, 10, 10], dtype=float), pattern)
        require(np.isnan(unknown), "Unobserved tail must stay unknown")
        cases.append({"inverted": inverted, "case": "unknown_tail_is_nan", "passed": True})
        pattern.break_p = 13.0 if inverted else 7.0
        zero = author.get_pattern_return(np.array([10, 10, pattern.break_p, 10]), pattern)
        require(zero == 0.0, "Self-terminal source event must retain zero source duration")
        cases.append({"inverted": inverted, "case": "self_terminal_confirmation", "passed": True})
        pattern.break_p = 7.0 if inverted else 13.0
        zero = author.get_pattern_return(np.array([10, 10, pattern.break_p, 10]), pattern)
        require(zero == 0.0, "Self-terminal stop cross must retain zero source duration")
        cases.append({"inverted": inverted, "case": "self_terminal_stop_cross", "passed": True})
    return cases


def equal_events(actual, expected):
    if actual.empty and expected.empty:
        return
    pd.testing.assert_frame_equal(
        actual.reset_index(drop=True), expected.reset_index(drop=True), check_exact=True
    )


def run():
    guard()
    protocol_path = STUDY / "protocol.json"
    protocol = json.loads(protocol_path.read_text())
    freeze = json.loads((STUDY / "implementation_freeze.json").read_text())
    for path, expected in freeze["files"].items():
        require(sha(Path(path)) == expected, f"Frozen implementation differs: {path}")
    author, ast_manifest = load_author()
    require(
        ast_manifest == json.loads((STUDY / "source_ast_manifest.json").read_text())["nodes"],
        "Source AST differs",
    )
    fixtures = source_exit_fixtures(author)
    records, all_events = [], []
    for pair_order, receipt in enumerate(protocol["inputs"]):
        guard()
        path = Path(receipt["path"])
        require(sha(path) == receipt["sha256"], "Frozen input differs")
        frame = pd.read_feather(path)
        validate_frame(frame)
        require(
            len(frame) == 27048 and frame.date.iloc[0] == pd.Timestamp(protocol["data_origin"]),
            "Source full calendar differs",
        )
        require(frame.date.iloc[-1] == pd.Timestamp(protocol["data_end"]), "Source end differs")
        events = extract_events(frame, author)
        checked = geometric_constraints(frame, events)
        cuts = [
            int(frame.date.searchsorted(pd.Timestamp(x)))
            for x in protocol["prefix_cutoffs_exclusive"]
        ]
        cuts.append(len(frame) - protocol["terminal_prefix_excluded_hours"])
        cases = []
        for cut in cuts:
            expected = (
                events.loc[events.break_i < cut].reset_index(drop=True)
                if not events.empty
                else events
            )
            prefix = extract_events(frame.iloc[:cut], author)
            equal_events(prefix, expected)
            shocked = frame.copy()
            shocked.loc[shocked.index[cut:], ["open", "high", "low", "close"]] *= 4.0
            changed = extract_events(shocked, author)
            before = (
                changed.loc[changed.break_i < cut].reset_index(drop=True)
                if not changed.empty
                else changed
            )
            equal_events(before, expected)
            cases.append(
                {
                    "prefix_rows": cut,
                    "confirmed_events": len(expected),
                    "prefix_exact": True,
                    "future_shock_exact": True,
                }
            )
        exported = events.copy()
        exported.insert(0, "pair_order", pair_order)
        exported.insert(1, "pair", receipt["pair"])
        exported["expiry_horizon_observed_in_snapshot"] = exported.source_expiry_i.lt(len(frame))
        exported["outcome_status"] = np.where(
            exported.expiry_horizon_observed_in_snapshot,
            "NOT_EVALUATED_NO_PNL",
            "UNKNOWN_TAIL_OUTCOME_NOT_EVALUATED",
        )
        exported.loc[exported.self_terminal_at_confirmation, "outcome_status"] = (
            "SELF_TERMINAL_EVENT_NO_ACCOUNT_TRADE_INFERRED"
        )
        all_events.append(exported)
        record = {
            **receipt,
            "pair_order": pair_order,
            "rows": len(frame),
            "source_events": len(events),
            "independent_constraints_checked": checked,
            "normal_events": int((~events.inverted).sum()),
            "inverse_events": int(events.inverted.sum()),
            "self_terminal_events": int(events.self_terminal_at_confirmation.sum()),
            "expiry_horizon_outside_snapshot_events": int(
                (~exported.expiry_horizon_observed_in_snapshot).sum()
            ),
            "unknown_tail_nonselfterminal_events": int(
                (
                    (~exported.expiry_horizon_observed_in_snapshot)
                    & (~events.self_terminal_at_confirmation)
                ).sum()
            ),
            "nonfinite_r2_events_retained": int((~np.isfinite(events.pattern_r2)).sum()),
            "source_events_at_same_bar": int(events.break_i.duplicated(keep=False).sum()),
            "prefix_cases": cases,
        }
        records.append(record)
        print(
            json.dumps(
                {
                    "pair": receipt["pair"],
                    "completed_pairs": len(records),
                    "events": len(events),
                    "prefix_shock_cases_passed": len(cases),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    combined = pd.concat(all_events, ignore_index=True)
    output = STUDY / "source_events.parquet"
    require(not output.exists(), "Refusing to overwrite source events")
    combined.to_parquet(output, index=False)
    receipt = {
        "verified_at": datetime.now(UTC).isoformat(),
        "passed": True,
        "scope": "Original ordered 40-pair source event admission; no account signals or returns",
        "protocol_sha256": sha(protocol_path),
        "ordered_pairs": protocol["ordered_pairs"],
        "pairs_checked": len(records),
        "input_rows": sum(r["rows"] for r in records),
        "source_events": len(combined),
        "source_event_rows_retained_without_selection": True,
        "prefix_and_future_shock_cases": sum(len(r["prefix_cases"]) for r in records),
        "geometric_constraints_checked": sum(r["independent_constraints_checked"] for r in records),
        "self_terminal_events": sum(r["self_terminal_events"] for r in records),
        "unknown_tail_nonselfterminal_events": sum(
            r["unknown_tail_nonselfterminal_events"] for r in records
        ),
        "source_exit_synthetic_fixtures": fixtures,
        "records": records,
        "event_artifact": {
            "path": str(output),
            "sha256": sha(output),
            "bytes": output.stat().st_size,
        },
        "resources": guard(),
        "original_forward_return_function_called_on_market_data": False,
        "overlap_or_allocation_policy_selected": False,
        "native_accounts_completed": 0,
        "financial_performance_proven": False,
        "orders_enabled": False,
        "goal_complete": False,
    }
    with (STUDY / "source_coverage_validation.json").open("x") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    with (STUDY / "pattern_metadata.json").open("x") as stream:
        json.dump(
            {
                "columns": list(combined.columns),
                "source_parameters": protocol["source_parameters"],
                "event_rows": len(combined),
                "first_usable_clock": "confirmation candle close, never pivot time",
                "return_columns_present": False,
                "expiry_horizon_flag_is_observation_coverage_not_a_realized_exit": True,
                "self_terminal_events_are_not_silently_deleted_or_counted_as_filled_trades": True,
                "overlap_capital_and_native_execution_policy": "UNREGISTERED_NO_ACCOUNT_SIGNALS",
            },
            stream,
            indent=2,
        )
        stream.write("\n")
    print(
        json.dumps(
            {
                "passed": True,
                "pairs": len(records),
                "events": len(combined),
                "resources": receipt["resources"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        with (STUDY / "validation_failure.json").open("x") as stream:
            json.dump(
                {
                    "at": datetime.now(UTC).isoformat(),
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "native_accounts_completed": 0,
                },
                stream,
                indent=2,
            )
            stream.write("\n")
        raise
