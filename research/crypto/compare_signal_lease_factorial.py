"""Describe every registered lifecycle/cost outcome without selecting accounts or pooling trades."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha


def cycle_details(entries):
    if not entries:
        return {"cycles": [], "positive_parent_fraction": None, "parents": 0}
    frame = pd.DataFrame(entries).sort_values(["open_timestamp", "pair"])
    frame["ordinal"] = frame.groupby("lease_id").cumcount() + 1
    parents = frame.groupby("lease_id").profit_abs.sum()
    cycles = []
    for number, rows in frame.groupby("ordinal", sort=True):
        wins, losses = rows.loc[rows.profit_abs > 0], rows.loc[rows.profit_abs < 0]
        mean_win = float(wins.profit_abs.mean()) if len(wins) else None
        mean_loss = float(-losses.profit_abs.mean()) if len(losses) else None
        cycles.append(
            {
                "ordinal": int(number),
                "positions": len(rows),
                "net_pnl_usdt": float(rows.profit_abs.sum()),
                "net_win_fraction": len(wins) / len(rows),
                "mean_win_usdt": mean_win,
                "mean_loss_usdt": mean_loss,
                "cash_break_even_win_fraction_if_means_fixed": mean_loss / (mean_win + mean_loss)
                if mean_win is not None and mean_loss is not None
                else None,
            }
        )
    return {
        "cycles": cycles,
        "positive_parent_fraction": float(parents.gt(0).mean()),
        "parents": len(parents),
        "worst_parent_net_pnl_usdt": float(parents.min()),
        "best_parent_net_pnl_usdt": float(parents.max()),
    }


def core_collisions(first, second):
    first_core = [r for r in first if r["sleeve"] == "core"]
    second_core = {(r["pair"], r["open_timestamp"]) for r in second if r["sleeve"] == "core"}
    occupied = []
    missing = [r for r in first_core if (r["pair"], r["open_timestamp"]) not in second_core]
    for row in missing:
        for child in second:
            if (
                child["sleeve"] == "small"
                and child["pair"] == row["pair"]
                and child["open_timestamp"] < row["open_timestamp"] < child["close_timestamp"]
            ):
                occupied.append(
                    {
                        "pair": row["pair"],
                        "core_entry_timestamp": row["open_timestamp"],
                        "occupied_small_open_timestamp": child["open_timestamp"],
                        "occupied_small_close_timestamp": child["close_timestamp"],
                        "missed_control_core_net_pnl_usdt": row["profit_abs"],
                    }
                )
    return {
        "control_core_entries_missing_in_variant": len(missing),
        "strictly_occupied_by_small_examples": occupied,
    }


def compare(folder):
    summary, review = read(folder / "summary.json"), read(folder / "independent_review.json")
    if not review["passed"] or review["summary_sha256"] != sha(folder / "summary.json"):
        raise ValueError("Missing current independent account review")
    if len(summary["accounts"]) != 12:
        raise ValueError("All four policies and three costs are required")
    accounts, tests, receipts = {}, {}, {}
    for item, checked in zip(summary["accounts"], review["accounts"], strict=True):
        score, audit = item["scorecard"], item["campaign_audit"]
        key = (score["candidate"], score["extra_cost_bps_per_side"])
        if key in accounts or checked["candidate"] != key[0]:
            raise ValueError("Duplicate or mismatched account")
        marked, whole = score["marked_account"], score["whole_campaigns"]
        small = audit["sleeves"]["small"]
        entries = [r for r in audit["lease_entry_receipts"] if r["sleeve"] == "small"]
        opens = pd.to_datetime([r["open_timestamp"] for r in entries], unit="ms", utc=True)
        lease_counts = pd.Series([r["lease_id"] for r in entries], dtype="str").value_counts()
        accounts[key] = {
            "candidate": key[0],
            "cost_bps": key[1],
            "net_return": marked["net_return"],
            "net_profit_usdt": marked["net_profit_usdt"],
            "sharpe_proxy": marked["binance_no_transfer_sharpe_proxy"],
            "hourly_marked_mdd": marked["hourly_marked_mdd"],
            "net_complete_position_win_rate": whole["net_win_rate"],
            "profit_factor": whole["profit_factor"],
            "complete_positions": whole["count"],
            "small_positions": small["closed_positions"],
            "small_net_profit_usdt": small["net_profit_usdt"],
            "small_net_win_rate": small["win_rate"],
            "small_profit_factor": small["profit_factor"],
            "small_complete_per_calendar_day": small["closed_positions"] / marked["calendar_days"],
            "small_entry_active_dates": len(opens.floor("D").unique()),
            "small_parent_leases_used": len(lease_counts),
            "small_leases_with_multiple_cycles": int(lease_counts.gt(1).sum()),
            "maximum_cycles_on_one_lease": int(lease_counts.max()) if len(lease_counts) else 0,
            "dependent_cycle_diagnostics": cycle_details(entries),
            "core_positions": audit["sleeves"]["core"]["closed_positions"],
            "core_net_profit_usdt": audit["sleeves"]["core"]["net_profit_usdt"],
            "core_net_win_rate": audit["sleeves"]["core"]["win_rate"],
            "project_joint_checks": checked["joint_discovery_checks"],
            "project_joint_checks_passed": checked["joint_discovery_passed"],
            "production_eligible": False,
        }
        tests[key] = checked["joint_discovery_passed"]
        receipts[key] = audit["lease_entry_receipts"]
    if set(accounts) != {
        (c, k)
        for c in ["LeaseOnce", "LeaseRecycle", "LeaseOnceProtect", "LeaseRecycleProtect"]
        for k in [0, 3, 10]
    }:
        raise ValueError("Changed factorial or cost population")
    fields = [
        "net_return",
        "sharpe_proxy",
        "hourly_marked_mdd",
        "net_complete_position_win_rate",
        "small_net_profit_usdt",
        "small_positions",
        "core_net_profit_usdt",
        "core_positions",
    ]
    effects = []
    for cost in [0, 3, 10]:
        for label, first, second in [
            ("recycle_without_core_protection", "LeaseOnce", "LeaseRecycle"),
            ("recycle_with_core_protection", "LeaseOnceProtect", "LeaseRecycleProtect"),
            ("core_protection_without_recycle", "LeaseOnce", "LeaseOnceProtect"),
            ("core_protection_with_recycle", "LeaseRecycle", "LeaseRecycleProtect"),
        ]:
            a, b = accounts[first, cost], accounts[second, cost]
            effects.append(
                {
                    "cost_bps": cost,
                    "effect": label,
                    "from": first,
                    "to": second,
                    "differences": {
                        f: b[f] - a[f] if a[f] is not None and b[f] is not None else None
                        for f in fields
                    },
                    "core_pair_occupancy_observation": core_collisions(
                        receipts[first, cost], receipts[second, cost]
                    ),
                }
            )
    names = sorted({k[0] for k in accounts})
    return {
        "compared_utc": datetime.now(UTC).isoformat(),
        "accounts": list(accounts.values()),
        "same_cost_factorial_differences": effects,
        "all_costs_joint_project_checks_passed": [
            n for n in names if all(tests[n, k] for k in [0, 3, 10])
        ],
        "summary_sha256": sha(folder / "summary.json"),
        "review_sha256": sha(folder / "independent_review.json"),
        "source_sha256": sha(Path(__file__)),
        "scope": "Descriptive factorial differences on previously observed2024H2."
        " No pooling of different accounts;parent scores are not small-cycle probabilities."
        " Project thresholds are not official exchange standards;"
        "historical gate success alone is not independent forward evidence or live qualification.",
        "production_eligible": False,
        "orders_enabled": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    args = parser.parse_args()
    output = args.folder / "factorial_comparison.json"
    if output.exists():
        raise ValueError("Preserve previous comparison")
    result = compare(args.folder)
    save(output, result)
    print(
        {
            "accounts": len(result["accounts"]),
            "all_costs_joint_project_passed": result["all_costs_joint_project_checks_passed"],
        }
    )
