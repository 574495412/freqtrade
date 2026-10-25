"""Independent scalar and Decimal replay of the frozen observed-quote labels."""

import argparse
import json
import math
from bisect import bisect_left
from decimal import Decimal, localcontext
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha


def valid_quote(row):
    if not row["valid"]:
        return False
    fields = [row[key] for key in ["age_upper_ms", "server_lower_ms", "server_upper_ms"]]
    if not all(math.isfinite(value) for value in fields):
        return False
    bid, ask = Decimal(row["bid_text"]), Decimal(row["ask_text"])
    return (
        0 < bid < ask
        and 0 <= row["age_upper_ms"] <= 1000
        and row["server_lower_ms"] <= row["server_upper_ms"]
    )


def scalar_path(rows, decision, sign, spec, capture_end):  # noqa: C901
    horizon = spec["maximum_holding_seconds"] * 1_000_000_000
    if decision + horizon + 4_000_000_000 >= capture_end:
        return {"known": False, "reason": "fixed_horizon_tail"}
    times = [int(row["mono_ns"]) for row in rows]
    validity = [valid_quote(row) for row in rows]
    bad = []
    for index, row in enumerate(rows):
        discontinuity = bool(row.get("source_gap", False))
        if index:
            previous = rows[index - 1]
            discontinuity |= row["mono_ns"] - previous["mono_ns"] > 1_000_000_000
            discontinuity |= row["epoch"] != previous["epoch"]
        bad.append(not validity[index] or discontinuity)

    def execute(target, delay, start=None):
        at = bisect_left(times, target)
        while at < len(rows) and times[at] <= target + delay:
            if validity[at]:
                return None if start is not None and any(bad[start : at + 1]) else at
            if start is not None:
                return None
            at += 1
        return None

    opening = execute(decision + 1_000_000_000, 1_000_000_000)
    if opening is None:
        return {"known": False, "reason": "missing_entry_quote"}
    with localcontext() as context:
        context.prec = 40
        entry = Decimal(rows[opening]["ask_text" if sign == 1 else "bid_text"])
        deadline = times[opening] + horizon
        reason, closing, trigger = "time", None, deadline
        take, stop = (
            Decimal(str(spec["take_price_fraction"])),
            Decimal(str(spec["stop_price_fraction"])),
        )
        for index in range(opening, len(rows)):
            if bad[index]:
                return {"known": False, "reason": "quote_path_gap_or_invalid"}
            if times[index] >= deadline:
                closing = execute(deadline, 1_000_000_000, opening)
                break
            quote = Decimal(rows[index]["bid_text" if sign == 1 else "ask_text"])
            gross = sign * (quote / entry - 1)
            if gross >= take or gross <= -stop:
                reason = "take" if gross >= take else "stop"
                trigger = times[index]
                closing = execute(trigger + 1_000_000_000, 1_000_000_000, opening)
                break
        if closing is None:
            return {"known": False, "reason": "missing_exit_quote"}
        exit_price = Decimal(rows[closing]["bid_text" if sign == 1 else "ask_text"])
        return {
            "known": True,
            "reason": reason,
            "entry_index": opening,
            "exit_index": closing,
            "entry_price": entry,
            "exit_price": exit_price,
            "trigger": trigger,
        }


def scalar_funding(rows, path, sign, source):
    entry, exit_quote = rows[path["entry_index"]], rows[path["exit_index"]]
    if (
        source["startTime"] > entry["server_lower_ms"]
        or source["endTime"] < exit_quote["server_upper_ms"]
    ):
        return {"known": False, "reason": "unknown_funding_coverage"}
    amount = Decimal(0)
    raw = source["raw_result"]
    body = raw.get("structuredContent")
    if body is None:
        texts = [item["text"] for item in raw["content"] if item["type"] == "text"]
        if len(texts) != 1:
            raise ValueError("Independent audit cannot parse funding source")
        body = json.loads(texts[0])
    events = body["result"] if isinstance(body, dict) else body
    for event in events:
        at, rate = event["fundingTime"], Decimal(event["fundingRate"])
        if at < entry["server_lower_ms"] or at >= exit_quote["server_upper_ms"] or rate == 0:
            continue
        if at < entry["server_upper_ms"] or at >= exit_quote["server_lower_ms"]:
            return {"known": False, "reason": "ambiguous_funding_exposure_boundary"}
        safe = [
            row
            for row in rows
            if row["server_upper_ms"] <= at
            and row["event_ms"] <= at
            and at - row["event_ms"] <= 1000
            and valid_quote(row)
        ]
        if not safe:
            return {"known": False, "reason": "missing_contemporaneous_funding_mid_proxy"}
        quote = safe[-1]
        mid = (Decimal(quote["bid_text"]) + Decimal(quote["ask_text"])) / 2
        amount -= sign * rate * mid
    return {"known": True, "cash": amount}


def audit(run, segments, output):  # noqa: C901
    if output.exists():
        raise ValueError("Preserve prior economic audit")
    expected_auditor = read(run / "input_freeze.json")["source_sha256"][
        "audit_microstructure_study.py"
    ]
    if sha(Path(__file__)) != expected_auditor:
        raise ValueError("Independent audit implementation changed since preregistered evaluation")
    for directory in [run, *segments]:
        for name, digest in read(directory / "receipts.json").items():
            if sha(directory / name) != digest:
                raise ValueError("Frozen audit input changed")
    sources = {}
    for segment in segments:
        if (
            sha(segment / "receipts.json")
            != read(run / "input_freeze.json")["segments"][segment.name]
        ):
            raise ValueError("Audit quote segment identity mismatch")
        sources[segment.name] = {"data": read(segment / "result.json"), "quotes": {}}
    plan = read(run / "plan.json")
    funding = {q["symbol"]: q for q in read(run / "funding_receipt.json")["queries"]}
    count, errors = 0, []
    labels = read(run / "labels.json")
    for label in labels:
        segment = next(path for path in segments if path.name == label["source_segment"])
        source = sources[segment.name]
        symbol = label["symbol"]
        if symbol not in source["quotes"]:
            source["quotes"][symbol] = pd.read_parquet(
                segment / f"{symbol}_quotes.parquet"
            ).to_dict("records")
        rows = source["quotes"][symbol]
        sign = label.get("sign")
        if sign is None:
            proposals = pd.read_parquet(run / f"{segment.name}_proposals.parquet")
            matched = proposals.loc[
                proposals.candidate.eq(label["candidate"])
                & proposals.symbol.eq(symbol)
                & proposals.decision_wall_ns.eq(label["decision_wall_ns"])
            ]
            if len(matched) != 1:
                raise ValueError("Audit proposal identity mismatch")
            sign = int(matched.sign.iloc[0])
        path = scalar_path(
            rows,
            label["decision_monotonic_ns"],
            sign,
            plan["quote_labels"],
            source["data"]["capture_end_monotonic_ns"],
        )
        carry = None
        if path["known"]:
            carry = scalar_funding(rows, path, sign, funding[symbol])
            if not carry["known"]:
                path.update(known=False, reason=carry["reason"])
        if path["known"] != label["known"] or path["reason"] != label["reason"]:
            raise ValueError("Independent path status or first trigger differs")
        count += 1
        if not path["known"]:
            continue
        if (
            path["entry_index"] != label["entry_index"]
            or path["exit_index"] != label["exit_index"]
            or path["trigger"] != label["trigger_monotonic_ns"]
        ):
            raise ValueError("Independent observed entry/exit indices differ")
        with localcontext() as context:
            context.prec = 40
            entry, exit_price = path["entry_price"], path["exit_price"]
            ratio = exit_price / entry
            for extra in plan["quote_labels"]["costs_per_side"]["extra_bps"]:
                fee = (
                    Decimal(str(plan["quote_labels"]["costs_per_side"]["base_fee"]))
                    + Decimal(extra) / 10_000
                )
                net = sign * (ratio - 1) + carry["cash"] / entry - fee * (1 + ratio)
                errors.append(abs(float(net) - label["net_labels"][str(extra)]))
    if errors and max(errors) > 1e-10:
        raise ValueError("Independent Decimal net quote labels differ")
    save(
        output,
        {
            "structural_replay_passed": True,
            "proposal_paths_replayed": count,
            "net_label_arithmetic_checks": len(errors),
            "net_label_arithmetic_passed": True if errors else None,
            "maximum_net_label_error": max(errors) if errors else None,
            "study_receipts_sha256": sha(run / "receipts.json"),
            "audit_source_sha256": sha(Path(__file__)),
            "limits": (
                "Independent scalar path and Decimal cash arithmetic on frozen quote sources; "
                "no exchange fills or account-return verification"
            ),
            "production_eligible": False,
            "orders_enabled": False,
        },
    )
    print(read(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--segments", type=Path, nargs=2, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.run.absolute(), [path.absolute() for path in args.segments], args.output.absolute())
