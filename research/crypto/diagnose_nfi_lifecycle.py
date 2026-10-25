"""Describe realized lifecycle interventions without treating partial exits as winning trades."""

import argparse
import math
from collections import Counter, defaultdict
from pathlib import Path

from freqtrade.research.data import read, save, sha
from research.crypto.audit_ensemble import raw_trades


def describe(account):
    result = read(account / "result.json")
    trades = raw_trades(account, result["candidate"])
    groups, rows, keys = defaultdict(list), [], set()
    tags, entries, exits = Counter(), 0, 0
    for trade in trades:
        sleeve = "core" if set(trade["enter_tag"].split()) == {"61"} else "small"
        groups[(sleeve, trade["exit_reason"])].append(trade)
        keys.add((trade["pair"], trade["open_timestamp"], trade["enter_tag"]))
        for order in trade["orders"]:
            if order["ft_is_entry"]:
                entries += 1
            else:
                exits += 1
                tags[order["ft_order_tag"] or "untagged"] += 1
    for (sleeve, reason), campaigns in sorted(groups.items()):
        rows.append(
            {
                "sleeve": sleeve,
                "exit_reason": reason,
                "complete_positions": len(campaigns),
                "net_profit_usdt": math.fsum(t["profit_abs"] for t in campaigns),
                "net_winrate": sum(t["profit_abs"] > 0 for t in campaigns) / len(campaigns),
                "mean_holding_minutes": math.fsum(t["trade_duration"] for t in campaigns)
                / len(campaigns),
            }
        )
    total = math.fsum(row["net_profit_usdt"] for row in rows)
    if abs(total - result["net_profit_usdt"]) > 1e-5:
        raise ValueError("Exit reason totals differ from complete account")
    negative_fills = tags["bounded_full_reduction"] + tags["bounded_partial_reduction"]
    return {
        "candidate": result["candidate"],
        "complete_positions": len(trades),
        "entry_fills": entries,
        "exit_fills": exits,
        "exit_order_tags": dict(tags),
        "source_negative_adjustment_fills_observed": negative_fills,
        "full_reduction_fill_intervention_observed": tags["bounded_full_reduction"] > 0,
        "missing_state_exit_count": sum(
            t["exit_reason"] == "missing_bounded_nfi_state" for t in trades
        ),
        "complete_position_groups": rows,
        "result_sha256": sha(account / "result.json"),
    }, keys


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    args = parser.parse_args()
    destination = args.folder / "lifecycle_diagnostics.json"
    if destination.exists():
        raise ValueError("Preserve prior diagnostics")
    summary = read(args.folder / "summary.json")
    observations, campaigns = [], {}
    for item in summary["accounts"]:
        account = Path(item["scorecard"]["source_result"]).parent
        record, keys = describe(account)
        observations.append(record)
        campaigns[record["candidate"]] = keys
    control = campaigns["NFIBoundedPartial"]
    comparison = [
        {
            "candidate": name,
            "same_initial_campaign_keys_as_control": len(keys & control),
            "additional_campaign_keys": len(keys - control),
            "control_campaign_keys_not_seen": len(control - keys),
        }
        for name, keys in campaigns.items()
    ]
    save(
        destination,
        {
            "accounts": observations,
            "campaign_overlap": comparison,
            "diagnostic_source_sha256": sha(Path(__file__)),
            "scope": "Realized whole-account paths, including capacity/reentry effects. "
            "Matching initial entries alone is not a controlled per-exit counterfactual. "
            "Zero full-reduction fills means that intervention was not economically tested. "
            "Partial exit fills never count as separate completed winning positions.",
            "orders_enabled": False,
            "production_eligible": False,
        },
    )
    print(
        {
            "accounts": len(observations),
            "exit_interventions": [x["exit_order_tags"] for x in observations],
        }
    )
