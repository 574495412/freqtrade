"""Attribute retained X8 fills and screen the native two-short-slot hypothesis."""

import json
from collections import defaultdict
from decimal import Decimal
from hashlib import file_digest
from itertools import groupby
from pathlib import Path
from zipfile import ZipFile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = ROOT / "user_data/crypto_quant"
CASES = {
    "futures_L_8bps": ("wide_strategy_comparison_20261105_v001", "nfi-x8-1x-cost8"),
    "futures_L_15bps": ("wide_strategy_cost15_20261105_v001", "nfi-x8-1x-cost15"),
    "futures_post_source_8bps": ("nfi40_post_source_account_20261105_v001", "nfi-x8-1x-cost8"),
    "spot_L_13bps": ("spot_baseline_long_run_20261105_v001", "nfi-x8-spot-cost13"),
    "spot_L_20bps": ("spot_baseline_long_run_20261105_v001", "nfi-x8-spot-cost20"),
}
CAP = 2
DEPLOYED = ROOT / "deploy/strategy/nfi-x8/NostalgiaForInfinityX8.py"


def sha(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def load_case(case):
    location = DATA.joinpath(*case)
    audit_file = location / "independent-account-audit.json"
    result_file = location / "result.json"
    audit = json.loads(audit_file.read_text())
    result = json.loads(result_file.read_text(), parse_float=Decimal)
    archives = list((location / "native_results").glob("*.zip"))
    if len(archives) != 1:
        raise ValueError("expected one sealed native archive")
    archive = archives[0]
    curve = location / "hourly_equity.parquet"
    receipts = {
        "archive_sha256": sha(archive),
        "result_sha256": sha(result_file),
        "curve_sha256": sha(curve),
    }
    for key, checksum in receipts.items():
        if audit[key] != checksum:
            raise ValueError(f"prior audit hash disagrees for {key}")
    if not all(
        audit[key]
        for key in (
            "common_nav_calendar_return_drawdown_checked",
            "native_fee_leverage_pair_capital_checked",
            "strategy_copy_checked",
        )
    ):
        raise ValueError("prior audit did not clear accounting controls")
    with ZipFile(archive) as exported:
        stem = archive.stem
        if exported.read(stem + "_NostalgiaForInfinityX8.py") != DEPLOYED.read_bytes():
            raise ValueError("native archived source differs from deployed X8")
        output = json.loads(exported.read(stem + ".json"), parse_float=Decimal)
    trades = output["strategy"]["NostalgiaForInfinityX8"]["trades"]
    if len(trades) != audit["trades"] or any(t["leverage"] != 1 for t in trades):
        raise ValueError("native trade count or leverage differs")
    return trades, result, {**receipts, "independent_audit_sha256": sha(audit_file)}


def summarize(trades):
    totals = {
        "trades": len(trades),
        "wins": sum(t["profit_abs"] > 0 for t in trades),
        "losses": sum(t["profit_abs"] < 0 for t in trades),
        "profit_usdt": str(sum(t["profit_abs"] for t in trades)),
        "funding_usdt": str(sum(t["funding_fees"] or 0 for t in trades)),
        "positions_with_adds": sum(sum(o["ft_is_entry"] for o in t["orders"]) > 1 for t in trades),
        "max_entry_fills_per_position": max(
            (sum(o["ft_is_entry"] for o in t["orders"]) for t in trades), default=0
        ),
        "max_stake_usdt": str(max((t["max_stake_amount"] for t in trades), default=0)),
        "longest_minutes": max((t["trade_duration"] for t in trades), default=0),
    }
    return totals


def classify_short_entries(shorts):
    """Attribute admission pressure in the original ledger, preserving timestamp ambiguity."""
    active = []
    cohorts = defaultdict(list)
    peak = 0
    for timestamp, batch in groupby(
        sorted(shorts, key=lambda t: t["open_timestamp"]), key=lambda t: t["open_timestamp"]
    ):
        simultaneous = list(batch)
        possible_active = [t for t in active if t["close_timestamp"] >= timestamp]
        definitely_active = [t for t in possible_active if t["close_timestamp"] > timestamp]
        peak = max(peak, len(definitely_active) + len(simultaneous))
        if len(definitely_active) >= CAP:
            key = "prior_open_shorts_already_at_cap"
        elif len(possible_active) + len(simultaneous) <= CAP:
            key = "original_entry_within_cap"
        else:
            key = "timestamp_batch_order_or_same_time_exit_matters"
        cohorts[key].extend(simultaneous)
        active = definitely_active + simultaneous
    return peak, cohorts


def attribute(trades, result):
    shorts = [t for t in trades if t["is_short"]]
    peak, cohorts = classify_short_entries(shorts)
    if abs(
        sum(t["profit_abs"] for t in trades) - result["native_terminal_return"] * 1000
    ) > Decimal(".000001"):
        raise ValueError("terminal attribution does not reconcile")
    groups = defaultdict(list)
    for t in trades:
        tags = t["enter_tag"].split()
        if t["exit_reason"] == "force_exit":
            key = "native_terminal_force_exit_separate"
        elif t["is_short"]:
            key = "short_with_562" if "562" in tags else "short_other"
        else:
            key = (
                "long_top_coins_only"
                if tags and set(tags) <= {"141", "142", "143", "144", "145"}
                else "long_other"
            )
        groups[key].append(t)
    if sum(map(len, groups.values())) != len(trades):
        raise ValueError("trade attribution is incomplete")
    return {
        "cost_bps_per_side": result["cost_bps_per_side"],
        "all_native_trades": summarize(trades),
        "disjoint_pnl_groups": {key: summarize(items) for key, items in groups.items()},
        "observed_peak_simultaneous_shorts_at_entry": peak,
        "two_short_slot_screen": {
            key: {
                "natural_closes": summarize([t for t in items if t["exit_reason"] != "force_exit"]),
                "terminal_force_exits": sum(t["exit_reason"] == "force_exit" for t in items),
                "with_562": sum("562" in t["enter_tag"].split() for t in items),
            }
            for key, items in cohorts.items()
        },
    }


def main():
    registration = HERE / "registration.json"
    plan = json.loads(registration.read_text())
    if sha(Path(__file__)) != plan["audit_code_sha256"] or sha(DEPLOYED) != plan["strategy_sha256"]:
        raise ValueError("registered code or strategy changed")
    result = {}
    for name, case in CASES.items():
        trades, summary, checksums = load_case(case)
        if checksums != plan["case_input_hashes"][name]:
            raise ValueError(f"registered inputs changed for {name}")
        result[name] = {**attribute(trades, summary), "input_hashes": checksums}
    (HERE / "attribution.json").write_text(
        json.dumps(
            {
                "registration_sha256": sha(registration),
                "classification": "retrospective attribution on retained native accounts",
                "new_financial_runs": 0,
                "candidate_profit_improvement_verified": False,
                "scenarios": result,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
