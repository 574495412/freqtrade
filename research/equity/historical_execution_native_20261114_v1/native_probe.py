"""Independent cash/quantity checks around actual guarded native fills."""

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from test_execution import LocalTrade, Trade, at, fill, probe, trade

HERE = Path(__file__).resolve().parent


def main():
    previous = Trade.use_db
    Trade.use_db = False
    LocalTrade.reset_trades()
    snapshots = []
    try:
        with patch.object(Trade, "commit"):
            a = probe()
            t = trade()

            def capture(label):
                orders = [
                    dict(
                        id=o.order_id,
                        pair=t.pair,
                        side=o.ft_order_side,
                        quantity=o.safe_filled,
                        price=o.safe_price,
                    )
                    for o in t.orders
                    if o.safe_filled
                ]
                cash = 1000 + sum(
                    o["quantity"] * o["price"] * (0.99 if o["side"] == "sell" else -1.01)
                    for o in orders
                )
                quantity = sum(o["quantity"] * (1 if o["side"] == "buy" else -1) for o in orders)
                assert abs(a.wallets.get_total("USD") - cash) < 1e-9
                assert abs(a.wallets.get_free("USD") + a.wallets.get_used("USD") - cash) < 1e-9
                actual_quantity = t.amount if t.is_open else 0
                assert abs(actual_quantity - quantity) < 1e-9
                snapshots.append(
                    dict(
                        label=label,
                        asof=a.wallets.asof.isoformat(),
                        orders=orders,
                        native_quote_cash=a.wallets.get_total("USD"),
                        native_free_cash=a.wallets.get_free("USD"),
                        native_restricted_cash=a.wallets.get_used("USD"),
                        native_held_quantity=actual_quantity,
                    )
                )

            capture("initial")
            fill(a, t, at(2), 5, 100)
            capture("guarded_native_entry")
            fill(a, t, at(3), 2, 110, False)
            capture("guarded_native_partial_exit")
            fill(a, t, at(6), 3, 90, False)
            capture("guarded_native_final_exit")
            a.wallets.advance(at(7, 22))
            capture("all_proceeds_released")
        result = dict(
            synthetic=True,
            historical_financial_accounts=0,
            funded_backtesting_start_called=False,
            return_metrics=None,
            native_fills=3,
            snapshots=snapshots,
            cash_and_quantity_reconciled=True,
            test_source_sha256=hashlib.sha256(
                (HERE / "test_execution.py").read_bytes()
            ).hexdigest(),
        )
        (HERE / "native_synthetic_trace.json").write_text(json.dumps(result, indent=2) + "\n")
        print("3 actual native synthetic fills / 5 snapshots; cash and quantities reconcile")
    finally:
        LocalTrade.reset_trades()
        Trade.use_db = previous


if __name__ == "__main__":
    main()
