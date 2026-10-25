"""Reconcile completed native contract screening fills without changing selection."""

import argparse
import math
import statistics
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from freqtrade.research.data import read, save, sha
from research.crypto.audit_ensemble import fill_cash, raw_trades


def audit(folder, candidate):
    result, plan = read(folder / "result.json"), read(folder / "plan.json")
    trades = raw_trades(folder, candidate)
    errors = [fill_cash(trade) for trade in trades]
    profit = math.fsum(trade["profit_abs"] for trade in trades)
    if len(trades) != result["closed_trades"] or abs(profit - result["net_profit_usdt"]) > 1e-5:
        raise ValueError("Native closed trades do not reconcile with reported account")
    fees = sum(
        Decimal(str(order["amount"]))
        * Decimal(str(order["safe_price"]))
        * Decimal(str(trade["fee_open"] if order["ft_is_entry"] else trade["fee_close"]))
        for trade in trades
        for order in trade["orders"]
    )
    days = (datetime.fromisoformat(result["end"]) - datetime.fromisoformat(result["start"])).days
    additions, partials = 0, 0
    for trade in trades:
        entries = [order for order in trade["orders"] if order["ft_is_entry"]]
        exits = [order for order in trade["orders"] if not order["ft_is_entry"]]
        additions += len(entries) - 1
        partials += len(exits) - 1
        if candidate == "PerpFlowFunded":
            if len(entries) > 2 or len(exits) > 2:
                raise ValueError("Funded campaign exceeded registered order counts")
            if len(entries) == 2:
                harvest = [o for o in exits if o["ft_order_tag"] == "half_profit_harvest"]
                if (
                    len(harvest) != 1
                    or entries[1]["ft_order_tag"] != "realized_profit_authorized_add"
                    or entries[1]["order_filled_timestamp"] <= harvest[0]["order_filled_timestamp"]
                ):
                    raise ValueError("Funded addition did not follow its filled harvest")
    return {
        "candidate": candidate,
        "folder": str(folder.absolute()),
        "plan_sha256": sha(folder / "plan.json"),
        "result_sha256": sha(folder / "result.json"),
        "native_zip_sha256": sha(next(folder.glob("backtest-result-*.zip"))),
        "extra_cost_bps_per_side": result["extra_cost_bps_per_side"],
        "closed_trades": len(trades),
        "complete_trades_per_calendar_day": len(trades) / days,
        "long_short_counts": dict(Counter("short" if t["is_short"] else "long" for t in trades)),
        "exit_reasons": dict(Counter(t["exit_reason"] for t in trades)),
        "median_holding_minutes": statistics.median(t["trade_duration"] for t in trades),
        "additional_entries": additions,
        "partial_exits": partials,
        "fees_usdt": str(fees),
        "funding_net_usdt": math.fsum(t["funding_fees"] for t in trades),
        "net_profit_usdt": profit,
        "pnl_before_fees_with_funding_usdt": profit + float(fees),
        "maximum_fill_cash_error_usdt": max(errors, default=0),
        "first_entry": min(t["open_date"] for t in trades),
        "last_close": max(t["close_date"] for t in trades),
        "initial_equity_usdt": plan["initial_equity_usdt"],
        "native_fill_cash_audit_passed": True,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Preserve existing audit output")
    root = Path("user_data/crypto_quant")
    studies = {
        "cluc2022_run_v001": ["Cluc2022"],
        "quickie2022_run_v001": ["Quickie2022"],
        "perpetual_flow_run_v001": ["PerpFlowFast", "PerpFlowFunded"],
    }
    accounts = [
        audit(root / study / "train" / candidate / f"cost_{cost}bps", candidate)
        for study, candidates in studies.items()
        for candidate in candidates
        for cost in [0, 3, 10]
    ]
    save(
        args.output,
        {
            "schema": "crypto-quant-contract-fill-audit.v1",
            "audit_source_sha256": sha(Path(__file__)),
            "accounts": accounts,
            "all_fill_cash_audits_passed": True,
            "limitations": [
                "Independent fill cash and inventory reconciliation, not a market-fill simulator",
                "Recorded funding reconciled; event timing is not independently replayed here",
                "Funded order counts and harvest-before-add checked",
                "Feature and risk-budget callbacks are not independently replayed here",
                "A passed accounting audit does not qualify a rejected economic candidate",
            ],
            "production_eligible": False,
            "orders_enabled": False,
        },
    )
    print(f"Reconciled {len(accounts)} native contract accounts")


if __name__ == "__main__":
    main()
