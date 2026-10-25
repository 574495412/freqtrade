"""Compare archived marked account metrics within identical periods and cost assumptions."""

import argparse
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from freqtrade.research.data import read, save, sha


def compare(paths):
    groups, rows, inputs = defaultdict(list), [], []
    for path in paths:
        document = read(path)
        inputs.append({"path": str(path.absolute()), "sha256": sha(path)})
        for item in document["accounts"]:
            score = item.get("scorecard", item)
            marked, whole = score["marked_account"], score["whole_campaigns"]
            row = {
                "id": str(Path(score["source_result"]).parent),
                "candidate": score["candidate"],
                "start": score["start"],
                "end": score["end"],
                "extra_cost_bps_per_side": score["extra_cost_bps_per_side"],
                "net_return": marked["net_return"],
                "sharpe_proxy": marked["binance_no_transfer_sharpe_proxy"],
                "hourly_marked_mdd": marked["hourly_marked_mdd"],
                "closed_positions": whole["count"],
                "whole_position_winrate": whole["net_win_rate"],
                "profit_factor": whole["profit_factor"],
                "net_expectancy_usdt": whole["net_expectancy_usdt"],
                "native_economic_screen_passed": score["old_economic_audit_unchanged"]["passed"],
                "source_result_sha256": score["input_identities"]["result_sha256"],
                "production_eligible": False,
            }
            if sha(Path(score["source_result"])) != row["source_result_sha256"]:
                raise ValueError("Archived account result changed")
            rows.append(row)
            groups[(row["start"], row["end"], row["extra_cost_bps_per_side"])].append(row)
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate account in comparison")
    cohorts = []
    for (start, end, cost), accounts in groups.items():
        adequate = [
            r for r in accounts if r["closed_positions"] >= 30 and r["sharpe_proxy"] is not None
        ]

        def dominates(a, b):
            av = (a["net_return"], a["sharpe_proxy"], -a["hourly_marked_mdd"])
            bv = (b["net_return"], b["sharpe_proxy"], -b["hourly_marked_mdd"])
            return all(x >= y for x, y in zip(av, bv, strict=True)) and any(
                x > y for x, y in zip(av, bv, strict=True)
            )

        frontier = [r["id"] for r in adequate if not any(dominates(a, r) for a in adequate)]
        cohorts.append(
            {
                "start": start,
                "end": end,
                "cost_bps": cost,
                "accounts": len(accounts),
                "at_least_30_positions_and_defined_sharpe": len(adequate),
                "return_sharpe_drawdown_frontier_ids": frontier,
            }
        )
    return {
        "generated_utc": datetime.now(UTC).isoformat(),
        "inputs": inputs,
        "source_sha256": sha(Path(__file__)),
        "accounts": rows,
        "account_count": len(rows),
        "cohorts": cohorts,
        "orders_enabled": False,
        "production_eligible": False,
        "scope": "Archived retrospective account comparisons. Maximize return/Sharpe and minimize "
        "hourly marked drawdown without weighted scores. Frontier membership is not validation or "
        "a live admission. Different periods and costs are never ranked together. Thirty positions "
        "is the existing research minimum, not an official exchange threshold.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Preserve previous comparison")
    result = compare(args.inputs)
    save(args.output, result)
    print({"account_count": result["account_count"], "cohorts": len(result["cohorts"])})
