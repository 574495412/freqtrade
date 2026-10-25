"""Freeze all three signals across both segments before any quote-return labels."""

import argparse
import sys
from collections import Counter
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.microstructure_funding import funding_receipt
from research.crypto.microstructure_proposals import NAMES, proposals
from research.crypto.microstructure_quotes import QuoteSeries, funding_cash, net_labels, quote_path
from research.crypto.prepare_microstructure_quotes import verify_receipts


FROZEN_SOURCES = [
    "microstructure_study.py",
    "microstructure_quotes.py",
    "microstructure_proposals.py",
    "microstructure_funding.py",
    "microstructure_features.py",
    "prepare_microstructure_quotes.py",
    "audit_microstructure_study.py",
]


def runtime_identity():
    return {
        "python": sys.version,
        "packages": {name: version(name) for name in ["numpy", "pandas", "pyarrow"]},
    }


def verify_code_freeze(path, plan_path, enrollment_path):
    frozen = read(path)
    if frozen["plan_sha256"] != sha(plan_path) or frozen["enrollment_sha256"] != sha(
        enrollment_path
    ):
        raise ValueError("Frozen economic protocol or enrollment changed")
    if frozen["runtime"] != runtime_identity() or set(frozen["source_sha256"]) != set(
        FROZEN_SOURCES
    ):
        raise ValueError("Frozen runtime or source set changed")
    for name, digest in frozen["source_sha256"].items():
        if sha(Path(__file__).with_name(name)) != digest:
            raise ValueError(f"Frozen economic implementation changed: {name}")
    return frozen


def screen_summary(labels, costs):
    results = {}
    for candidate in NAMES:
        rows = [label for label in labels if label["candidate"] == candidate]
        known = [label for label in rows if label["known"]]
        metrics = {}
        for extra in costs:
            values = [label["net_labels"][str(extra)] for label in known]
            metrics[str(extra)] = {
                "mature_known_labels": len(values),
                "positive_net_label_fraction": float(np.mean(np.asarray(values) > 0))
                if values
                else None,
                "mean_unit_net_label": float(np.mean(values)) if values else None,
            }
        results[candidate] = {
            "proposals": len(rows),
            "known_labels": len(known),
            "unknown_or_purged_reasons": dict(
                Counter(label["reason"] for label in rows if not label["known"])
            ),
            "costs": metrics,
            "overlapping_labels_are_not_account_pnl": True,
        }
    return results


def admission(summary, rule, source_engineering_passed):
    eligible = []
    for candidate, result in summary.items():
        passed = source_engineering_passed and all(
            cost["mature_known_labels"] >= rule["minimum_mature_signals_each_candidate"]
            and cost["positive_net_label_fraction"] >= rule["minimum_positive_net_label_fraction"]
            and cost["mean_unit_net_label"] > 0
            for cost in result["costs"].values()
        )
        if passed:
            eligible.append(candidate)
    return eligible


def study(plan_path, enrollment_path, segments, funding_path, output, code_freeze):  # noqa: C901
    if output.exists():
        raise ValueError("Preserve prior economic study")
    plan, enrollment = read(plan_path), read(enrollment_path)
    if sha(plan_path) != enrollment["economic_plan_sha256"]:
        raise ValueError("Enrollment economic protocol changed")
    if len(segments) != len(enrollment["discovery_segments"]):
        raise ValueError("Both enrolled segments required before economic screening")
    frozen = verify_code_freeze(code_freeze, plan_path, enrollment_path)
    for segment, expected in zip(segments, enrollment["discovery_segments"], strict=True):
        verify_receipts(segment)
        if read(segment / "result.json")["source_capture"] != expected:
            raise ValueError("Segment enrollment identity mismatch")
        if read(segment / "input_freeze.json")["economic_plan_sha256"] != sha(plan_path):
            raise ValueError("Quote source belongs to a different economic protocol")
        normalized_sources = read(segment / "input_freeze.json")["sources"]
        if set(normalized_sources) != {
            "prepare_microstructure_quotes.py",
            "microstructure_quotes.py",
            "microstructure_features.py",
        }:
            raise ValueError("Normalized quote source set changed")
        for name, digest in normalized_sources.items():
            if frozen["source_sha256"].get(name) != digest:
                raise ValueError("Normalized quotes use a different frozen implementation")
    output.mkdir(parents=True)
    sources = output / "sources"
    sources.mkdir()
    for name in FROZEN_SOURCES:
        source = Path(__file__).with_name(name)
        (sources / name).write_bytes(source.read_bytes())
    save(output / "plan.json", plan)
    save(output / "enrollment.json", enrollment)
    (output / "code_freeze.json").write_bytes(code_freeze.read_bytes())
    (output / "funding_receipt.json").write_bytes(funding_path.read_bytes())
    save(
        output / "input_freeze.json",
        {
            "plan_sha256": sha(plan_path),
            "enrollment_sha256": sha(enrollment_path),
            "code_freeze_sha256": sha(code_freeze),
            "segments": {segment.name: sha(segment / "receipts.json") for segment in segments},
            "funding_receipt_sha256": sha(funding_path),
            "source_sha256": {p.name: sha(p) for p in sources.iterdir()},
            "interpretation": (
                "Check80percent positive net labels in every registered cost scenario; "
                "no threshold search"
            ),
            "labels_are_not_account_returns": True,
            "orders_enabled": False,
        },
    )
    inputs, populations, prior_end_wall = [], [], None
    for segment in segments:
        data = read(segment / "result.json")
        quotes = {
            symbol: QuoteSeries(pd.read_parquet(segment / f"{symbol}_quotes.parquet"))
            for symbol in plan["symbols"]
        }
        trades = {
            symbol: pd.read_parquet(segment / f"{symbol}_trade_clock.parquet")
            for symbol in plan["symbols"]
        }
        first = min(q.frame.wall_ns.min() for q in quotes.values())
        last = max(q.frame.wall_ns.max() for q in quotes.values())
        if prior_end_wall is not None and first <= prior_end_wall:
            raise ValueError("Discovery segments overlap or are not chronological")
        prior_end_wall = last
        proposed, population = proposals(
            pd.read_parquet(segment / "features.parquet"), quotes, trades, plan
        )
        proposed["source_segment"] = segment.name
        proposed.to_parquet(output / f"{segment.name}_proposals.parquet", index=False)
        populations.append({"segment": segment.name, "population": population})
        inputs.append((segment, data, quotes, proposed))
    save(output / "population.json", populations)
    save(
        output / "proposal_freeze.json",
        {
            "sha256": {path.name: sha(path) for path in output.glob("*_proposals.parquet")},
            "forward_quote_labels_computed": False,
        },
    )
    # Every candidate and segment has frozen proposals before this first outcome calculation.
    funding = funding_receipt(read(funding_path), plan["symbols"])
    for _, _, quotes, _ in inputs:
        for symbol, series in quotes.items():
            lower = series.frame.server_lower_ms.min()
            upper = series.frame.server_upper_ms.max()
            coverage = funding[symbol]["coverage"]
            if (
                not np.isfinite([lower, upper]).all()
                or coverage["start_ms"] > lower
                or coverage["end_ms"] < upper
            ):
                raise ValueError("Funding receipt must cover the entire captured source interval")
    labels = []
    for segment, data, quotes, proposed in inputs:
        for row in proposed.itertuples():
            q = quotes[row.symbol]
            path = quote_path(
                q,
                int(row.decision_monotonic_ns),
                int(row.sign),
                plan["quote_labels"],
                data["capture_end_monotonic_ns"],
            )
            result = {
                "source_segment": segment.name,
                "candidate": row.candidate,
                "symbol": row.symbol,
                "decision_wall_ns": int(row.decision_wall_ns),
                "decision_monotonic_ns": int(row.decision_monotonic_ns),
                **path,
            }
            if path["known"]:
                source = funding[row.symbol]
                carry = funding_cash(q, path, source["events"], source["coverage"])
                result["funding"] = carry
                if carry["known"]:
                    result["net_labels"] = net_labels(
                        path, carry, plan["quote_labels"]["costs_per_side"]
                    )
                else:
                    result.update(known=False, reason=carry["reason"])
            labels.append(result)
    save(output / "labels.json", labels)
    summary = screen_summary(labels, plan["quote_labels"]["costs_per_side"]["extra_bps"])
    source_ready = all(data["source_engineering_passed"] for _, data, _, _ in inputs)
    eligible = admission(summary, plan["prediction_admission"], source_ready)
    save(
        output / "screen.json",
        {
            "candidates": summary,
            "cash": {"proposals": 0, "trades": 0, "qualifies_for_goal": False},
            "source_engineering_passed_both_segments": source_ready,
            "account_returns_or_winrates_measured": False,
        },
    )
    save(
        output / "admission.json",
        {
            "quote_label_screen_eligible": eligible,
            "core_sleeve_qualified": False,
            "joint_account_objective_achieved": False,
            "production_eligible": False,
            "orders_enabled": False,
            "next": "native_same_account_and_later_independent_forward_required"
            if eligible
            else "new_hypothesis_required_keep_failures",
        },
    )
    for name, digest in read(output / "input_freeze.json")["source_sha256"].items():
        if sha(Path(__file__).with_name(name)) != digest:
            raise ValueError("Economic source changed during evaluation")
    save(
        output / "receipts.json",
        {str(p.relative_to(output)): sha(p) for p in sorted(output.rglob("*")) if p.is_file()},
    )
    print({"quote_label_screen_eligible": eligible, "production_eligible": False})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--enrollment", type=Path, required=True)
    parser.add_argument("--segments", type=Path, nargs=2, required=True)
    parser.add_argument("--funding-receipt", type=Path, required=True)
    parser.add_argument("--code-freeze", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    study(
        args.plan.absolute(),
        args.enrollment.absolute(),
        [p.absolute() for p in args.segments],
        args.funding_receipt.absolute(),
        args.output.absolute(),
        args.code_freeze.absolute(),
    )
