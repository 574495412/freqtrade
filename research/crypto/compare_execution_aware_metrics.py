"""Compare existing account cards only within compatible cost and execution conventions."""

import argparse
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from freqtrade.research.data import read, save, sha
from research.crypto.compare_precision_aware_metrics import compare as previous_compare


def compare(paths):
    result = previous_compare(paths)
    groups = defaultdict(list)
    for row in result["accounts"]:
        account = Path(row["id"])
        plan = read(account / "plan.json")
        adapter_path = account / "runtime_adapter.json"
        adapter = read(adapter_path) if adapter_path.exists() else {}
        detail = plan.get("settings", {}).get("timeframe_detail")
        execution = adapter.get("execution_convention")
        if execution is None:
            if detail:
                raise ValueError("Detail account lacks an explicit execution convention")
            execution = f"native-{plan['timeframe']}-main-without-detail"
        row.update(
            execution_convention=execution,
            base_fee_per_side=plan["fee_per_side"],
            initial_equity_usdt=plan["initial_equity_usdt"],
            runtime_adapter_sha256=sha(adapter_path) if adapter_path.exists() else None,
        )
        key = (
            row["start"],
            row["end"],
            row["extra_cost_bps_per_side"],
            row["base_fee_per_side"],
            row["initial_equity_usdt"],
            row["precision_convention"],
            execution,
        )
        groups[key].append(row)
    cohorts = []
    for key, accounts in groups.items():
        adequate = [
            r for r in accounts if r["closed_positions"] >= 30 and r["sharpe_proxy"] is not None
        ]

        def dominates(a, b):
            av = (a["net_return"], a["sharpe_proxy"], -a["hourly_marked_mdd"])
            bv = (b["net_return"], b["sharpe_proxy"], -b["hourly_marked_mdd"])
            return all(x >= y for x, y in zip(av, bv, strict=True)) and any(
                x > y for x, y in zip(av, bv, strict=True)
            )

        cohorts.append(
            {
                **dict(
                    zip(
                        [
                            "start",
                            "end",
                            "cost_bps",
                            "base_fee_per_side",
                            "initial_equity_usdt",
                            "precision_convention",
                            "execution_convention",
                        ],
                        key,
                        strict=True,
                    )
                ),
                "accounts": len(accounts),
                "at_least_30_positions_and_defined_sharpe": len(adequate),
                "return_sharpe_drawdown_frontier_ids": [
                    r["id"] for r in adequate if not any(dominates(a, r) for a in adequate)
                ],
            }
        )
    result.update(
        generated_utc=datetime.now(UTC).isoformat(),
        source_sha256=sha(Path(__file__)),
        predecessor_source_sha256=sha(
            Path(__file__).with_name("compare_precision_aware_metrics.py")
        ),
        cohorts=cohorts,
        scope=(
            "Retrospective archives, not independent strategies or forward evidence. Compare net "
            "return, daily marked Sharpe proxy and hourly marked drawdown within identical "
            "periods, "
            "base fees, extra costs, initial equity, precision and execution conventions. Native "
            "5m and gated1m detail accounts never share a frontier. Frontier membership and the "
            "project minimum30 positions are not official certification or live qualification."
        ),
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Preserve previous comparison")
    result = compare(args.inputs)
    save(args.output, result)
    print({"accounts": result["account_count"], "execution_cohorts": len(result["cohorts"])})
