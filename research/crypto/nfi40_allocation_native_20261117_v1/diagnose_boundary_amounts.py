"""Raw rate/mark and single-event quantity amounts, never an uncertainty bound."""

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pandas as pd


HERE = Path(__file__).absolute().parent


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(candidate):
    freeze = read(HERE / "boundary_amount_freeze.json")
    for path, digest in freeze["files"].items():
        assert sha(path) == digest
    plan = read(HERE / "plan.json")
    assert candidate in [c["name"] for c in plan["candidates"]]
    directory = Path(plan["output_root"]) / candidate
    boundary_path = directory / "funding_boundary_identifiability.json"
    evidence = read(boundary_path)
    receipts = read(plan["data_receipts"]["path"])
    rows, hashes = [], {}
    for event in evidence["unknown_events"]:
        stem = event["pair"].replace("/", "_").replace(":", "_")
        selected = {}
        for suffix, column, key in [
            ("1h-funding_rate", "funding_rate", "funding_rate"),
            ("1h-mark", "open", "mark_open"),
        ]:
            relative = "futures/" + stem + "-" + suffix + ".feather"
            path = Path(plan["datadir"]) / relative
            digest = sha(path)
            assert digest == receipts[relative]
            hashes[str(path)] = digest
            frame = pd.read_feather(path)
            matching = frame.loc[
                pd.DatetimeIndex(frame.date).as_unit("ms").asi8 == event["timestamp_ms"]
            ]
            assert len(matching) == 1
            selected[key] = Decimal(str(matching.iloc[0][column]))
        rate, mark = selected["funding_rate"], selected["mark_open"]
        before, after = (
            Decimal(event["gross_quantity_before"]),
            Decimal(event["gross_quantity_after"]),
        )
        assert rate.is_finite() and mark.is_finite() and mark > 0
        unit = rate * mark
        cash_unit = unit if event["is_short"] else -unit
        rows.append(
            {
                "candidate": candidate,
                "pair": event["pair"],
                "archive_order_index": event["archive_order_index"],
                "timestamp_UTC": event["timestamp_UTC"],
                "is_short": event["is_short"],
                "classification": event["classification"],
                "source_funding_rate": str(rate),
                "source_mark_open_usdt": str(mark),
                "funding_rate_times_mark_usdt_per_coin": str(unit),
                "direction_signed_single_event_cash_usdt_per_coin": str(cash_unit),
                "gross_quantity_before": str(before),
                "gross_quantity_after": str(after),
                "single_event_cash_if_Qbefore_usdt": str(before * cash_unit),
                "single_event_cash_if_Qafter_usdt": str(after * cash_unit),
                "arithmetic_Qafter_minus_Qbefore_cash_usdt": str((after - before) * cash_unit),
                "funding_boundary_identifiability": "UNKNOWN",
            }
        )
    result = {
        "candidate": candidate,
        "source_boundary_sha256": sha(boundary_path),
        "audit_code_sha256": sha(__file__),
        "source_file_sha256": hashes,
        "events": rows,
        "scope": "Scalar single-event amounts at Qbefore and Qafter only",
        "complete_uncertainty_bound": False,
        "unique_boundary_proof": False,
        "alternative_full_account_return_computed": False,
        "numeric_auditor_or_tolerances_changed": False,
        "financial_replays_started": 0,
        "warning": "These amounts do not enumerate all native timing/state alternatives or bound full-account effects.",
    }
    path = directory / "funding_boundary_amount_diagnostics.json"
    with path.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(
        json.dumps(
            {
                "candidate": candidate,
                "events": rows,
                "complete_uncertainty_bound": False,
                "unique_boundary_proof": False,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    main(parser.parse_args().candidate)
