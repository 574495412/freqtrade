"""Native fill decomposition at the common marked endpoint; no signal imports."""

# ruff: noqa: S101
# Explicit independent audit assertions; optimized Python is rejected.
import importlib.util
import json
import math
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent
if not __debug__:
    raise RuntimeError("Endpoint audit requires assertions")


def main():
    path = HERE.with_name("trendcore_source_native_20261117_v1") / "audit_accounts.py"
    spec = importlib.util.spec_from_file_location("unchanged_native_archive_reader", path)
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    plan = json.loads((HERE / "plan_v2.json").read_text())
    edge = pd.Timestamp(plan["comparison_end"])
    rows = []
    marks = {
        p: float(
            pd.read_feather(Path(plan["datadir"]) / (p.replace("/", "_") + "-1h.feather"))
            .set_index("date")
            .at[edge, "open"]
        )
        for p in plan["pairs"]
    }
    for c in plan["candidates"]:
        directory = Path(plan["output_root"]) / (c["name"] + f"_{c['cost_bps']}bps")
        stats, _ = audit.native_stats(directory, c["strategy"])
        realized, unrealized, fees, reserves, open_positions, excluded = [], [], [], [], [], []
        included = 0
        for trade in stats["trades"]:
            quantity, cost = 0.0, 0.0
            for order in trade["orders"]:
                if order["order_filled_timestamp"] >= edge.timestamp() * 1000:
                    excluded.append(
                        {
                            "pair": trade["pair"],
                            "exit_reason": trade["exit_reason"],
                            "timestamp": order["order_filled_timestamp"],
                        }
                    )
                    continue
                q, value = order["amount"], order["amount"] * order["safe_price"]
                included += 1
                fees.append(
                    value * (trade["fee_open"] if order["ft_is_entry"] else trade["fee_close"])
                )
                if order["ft_is_entry"]:
                    quantity += q
                    cost += value
                else:
                    assert quantity > 0 and q <= quantity + 1e-9
                    basis = cost / quantity
                    realized.append(value - q * basis)
                    quantity -= q
                    cost -= q * basis
                    if abs(quantity) < 1e-10:
                        quantity, cost = 0.0, 0.0
            marked = quantity * marks[trade["pair"]]
            unrealized.append(marked - cost)
            reserves.append(marked * trade["fee_close"])
            if quantity > 0:
                open_positions.append(
                    {
                        "pair": trade["pair"],
                        "quantity": quantity,
                        "gross_unrealized_usdt": marked - cost,
                    }
                )
        total = math.fsum(realized) + math.fsum(unrealized) - math.fsum(fees) - math.fsum(reserves)
        nav = (
            pd.read_parquet(directory / "hourly_equity.parquet")
            .set_index("date")
            .hourly_mark_equity
        )
        error = abs(total - (nav.loc[edge] - c["capital"]))
        assert error < 1e-7
        assert len(excluded) == len(open_positions)
        assert all(r["exit_reason"] == "force_exit" for r in excluded)
        rows.append(
            {
                "account": c["name"],
                "gross_realized_price_pnl_usdt": math.fsum(realized),
                "gross_unrealized_price_pnl_usdt": math.fsum(unrealized),
                "paid_quote_fees_usdt": math.fsum(fees),
                "remaining_exit_fee_reserve_usdt": math.fsum(reserves),
                "net_pnl_usdt": total,
                "common_window_simulated_fills": included,
                "excluded_native_forced_exits": excluded,
                "open_positions": open_positions,
                "reconciliation_error_usdt": error,
            }
        )
    (HERE / "endpoint_audit.json").write_text(
        json.dumps({"passed": True, "accounts": rows}, indent=2) + "\n"
    )
    print(json.dumps(rows))


if __name__ == "__main__":
    main()
