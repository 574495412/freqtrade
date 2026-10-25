"""Describe terminal-label / executed-path differences without selecting a new strategy."""

import argparse
import math
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.audit_ensemble import raw_trades


def diagnose(folder, learning):
    summary_path = folder / "summary.json"
    review_path = folder / "independent_review.json"
    review = read(review_path)
    if not review["passed"] or review["summary_sha256"] != sha(summary_path):
        raise ValueError("Original account review is missing or stale")
    labels_path = learning / "evaluation/validation_units.parquet"
    predictions_path = learning / "learning/selected_predictions.parquet"
    evaluation = read(learning / "evaluation/result.json")
    receipt = read(learning / "learning/receipt.json")
    if (
        sha(predictions_path) != receipt["selected_predictions_sha256"]
        or sha(labels_path) != evaluation["validation_units_sha256"]
    ):
        raise ValueError("Frozen forecasts or terminal labels changed")
    labels = pd.read_parquet(labels_path).set_index("row_id", verify_integrity=True)
    predictions = pd.read_parquet(predictions_path)
    plan = read(folder / "plan.json")
    records = []
    for account in read(summary_path)["accounts"]:
        name = account["result"]["candidate"]
        policy = next(c["policy"] for c in plan["candidates"] if c["name"] == name)
        score = account["scorecard"]
        path = Path(score["source_result"]).parent
        if sha(path / "result.json") != score["input_identities"]["result_sha256"]:
            raise ValueError("Original result changed")
        trades = raw_trades(path, name)
        rows = account["campaign_audit"]["frozen_source_row_ids"]
        pairs = []
        for trade, row_id in zip(trades, rows, strict=True):
            label = labels.loc[row_id]
            entry = pd.to_datetime(trade["open_timestamp"], unit="ms", utc=True)
            if (
                label.entry != entry
                or label.symbol != trade["pair"].split("/")[0] + "USDT"
                or label.side != (-1 if trade["is_short"] else 1)
                or label.sleeve != "core"
                or not math.isclose(label.entry_price, trade["open_rate"], abs_tol=1e-10)
            ):
                raise ValueError("Terminal label and executed entry do not match")
            notional = trade["amount"] * trade["open_rate"]
            pairs.append(
                {
                    "row_id": row_id,
                    "exit_reason": trade["exit_reason"],
                    "is_short": trade["is_short"],
                    "native_net_pnl_usdt": trade["profit_abs"],
                    "native_net_per_entry_notional": trade["profit_abs"] / notional,
                    "matched_terminal_net_unit_label": float(label.net_label_3bps),
                }
            )
        paired = pd.DataFrame(pairs)
        selected = predictions.loc[
            predictions.model.eq(policy) & predictions.sleeve.eq("core")
        ]
        original = labels.loc[selected.row_id].net_label_3bps
        exits = []
        for reason, group in paired.groupby("exit_reason", sort=True):
            exits.append(
                {
                    "exit_reason": reason,
                    "complete_positions": len(group),
                    "actual_net_pnl_usdt": math.fsum(group.native_net_pnl_usdt),
                    "actual_positive_complete_fraction": float(
                        group.native_net_pnl_usdt.gt(0).mean()
                    ),
                    "matched_terminal_label_positive_fraction": float(
                        group.matched_terminal_net_unit_label.gt(0).mean()
                    ),
                }
            )
        records.append(
            {
                "candidate": name,
                "forecast_opportunities": len(selected),
                "executed_complete_positions": len(paired),
                "opportunity_acceptance_fraction": len(paired) / len(selected),
                "all_selected_mean_terminal_unit_label": float(original.mean()),
                "accepted_rows_mean_terminal_unit_label": float(
                    paired.matched_terminal_net_unit_label.mean()
                ),
                "actual_mean_net_per_entry_notional": float(
                    paired.native_net_per_entry_notional.mean()
                ),
                "accepted_terminal_label_positive_fraction": float(
                    paired.matched_terminal_net_unit_label.gt(0).mean()
                ),
                "actual_net_complete_position_winrate": float(
                    paired.native_net_pnl_usdt.gt(0).mean()
                ),
                "positive_terminal_label_but_nonpositive_native_positions": int(
                    (
                        paired.matched_terminal_net_unit_label.gt(0)
                        & paired.native_net_pnl_usdt.le(0)
                    ).sum()
                ),
                "nonpositive_terminal_label_but_positive_native_positions": int(
                    (
                        paired.matched_terminal_net_unit_label.le(0)
                        & paired.native_net_pnl_usdt.gt(0)
                    ).sum()
                ),
                "exit_attribution": exits,
                "monthly_marked_returns": score["monthly_marked_returns"],
            }
        )
    return {
        "generated_utc": datetime.now(UTC).isoformat(),
        "inputs": {
            str(path): sha(path)
            for path in [summary_path, review_path, labels_path, predictions_path]
        },
        "source_sha256": sha(Path(__file__)),
        "accounts": records,
        "scope": "Post-outcome descriptive paired comparison of exactly the same coin, side, "
        "entry time and entry price. Terminal labels ignore stops and capacity. Their means are "
        "not account returns or a no-stop strategy backtest. Stopping changes later capacity, "
        "so this does not identify an independently tradable counterfactual or justify removing "
        "risk controls. No refits, thresholds, policies, old verdicts or forecasts changed.",
        "orders_enabled": False,
        "production_eligible": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--learning", type=Path, required=True)
    args = parser.parse_args()
    output = args.folder / "postscreen_path_diagnostics.json"
    if output.exists():
        raise ValueError("Preserve previous diagnostics")
    result = diagnose(args.folder, args.learning)
    save(output, result)
    print({"accounts_diagnosed": len(result["accounts"]), "admission_changed": False})
