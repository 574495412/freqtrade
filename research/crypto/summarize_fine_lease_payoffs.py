"""Describe completed, independently reviewed minute-detail accounts without selecting rules."""

import argparse
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.audit_ensemble import raw_trades


def payoff(trades):
    wins = [t["profit_abs"] for t in trades if t["profit_abs"] > 0]
    losses = [-t["profit_abs"] for t in trades if t["profit_abs"] < 0]
    gain = math.fsum(wins) / len(wins) if wins else None
    loss = math.fsum(losses) / len(losses) if losses else None
    return {
        "complete_positions": len(trades),
        "net_profit_usdt": math.fsum(t["profit_abs"] for t in trades),
        "win_rate": len(wins) / len(trades) if trades else None,
        "mean_winner_usdt": gain,
        "mean_loser_abs_usdt": loss,
        "break_even_win_rate_if_mean_payoffs_fixed": loss / (gain + loss)
        if gain is not None and loss is not None
        else None,
    }


def summarize(folder):
    summary, review = read(folder / "summary.json"), read(folder / "independent_review.json")
    if not summary["all_registered_accounts_terminal"] or not review["passed"]:
        raise ValueError("Completed native and independently reviewed accounts required")
    if review["summary_sha256"] != sha(folder / "summary.json"):
        raise ValueError("Review no longer matches summary")
    rows = []
    for item in summary["accounts"]:
        score, audit = item["scorecard"], item["campaign_audit"]
        account = Path(score["source_result"]).parent
        trades = raw_trades(account, score["candidate"])
        small = [t for t in trades if t["enter_tag"] == "small_first_hit"]
        exits, leases, ordinals = defaultdict(list), defaultdict(list), defaultdict(list)
        receipts = [r for r in audit["lease_entry_receipts"] if r["sleeve"] == "small"]
        lease_by_open = {(r["pair"], r["open_timestamp"]): r["lease_id"] for r in receipts}
        for trade in sorted(small, key=lambda t: t["open_timestamp"]):
            lease = lease_by_open[(trade["pair"], trade["open_timestamp"])]
            leases[lease].append(trade)
            ordinals[len(leases[lease])].append(trade)
            exits[trade["exit_reason"]].append(trade)
        days = score["marked_account"]["calendar_days"]
        rows.append(
            {
                "candidate": score["candidate"],
                "cost_bps": score["extra_cost_bps_per_side"],
                "marked_account": score["marked_account"],
                "whole_campaigns": score["whole_campaigns"],
                "sleeves": audit["sleeves"],
                "small_payoff": payoff(small),
                "small_per_calendar_day": len(small) / days,
                "small_distinct_entry_days": len(
                    {pd.to_datetime(t["open_timestamp"], unit="ms", utc=True).date() for t in small}
                ),
                "small_parent_leases": len(leases),
                "small_profitable_parent_leases": sum(
                    math.fsum(t["profit_abs"] for t in group) > 0 for group in leases.values()
                ),
                "small_by_exit": {key: payoff(group) for key, group in exits.items()},
                "small_by_conditional_cycle": {
                    key: payoff(group) for key, group in ordinals.items()
                },
                "unpriced_execution_steps_checked": audit["unpriced_execution_steps_checked"],
                "account_result_sha256": sha(account / "result.json"),
            }
        )
    return {
        "recorded_utc": datetime.now(UTC).isoformat(),
        "accounts": rows,
        "summary_sha256": sha(folder / "summary.json"),
        "independent_review_sha256": sha(folder / "independent_review.json"),
        "source_sha256": sha(Path(__file__)),
        "scope": "Retrospective realized complete positions. Cycle groups are conditional on prior "
        "net wins, not independent samples. A mean-payoff break-even win rate holds observed mean "
        "payoffs fixed; it is not a forecast or an official target. No parameter selection.",
        "orders_enabled": False,
        "production_eligible": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    args = parser.parse_args()
    path = args.folder / "payoff_diagnostics.json"
    if path.exists():
        raise ValueError("Preserve previous diagnostic")
    result = summarize(args.folder)
    save(path, result)
    print({"accounts_described": len(result["accounts"])})
