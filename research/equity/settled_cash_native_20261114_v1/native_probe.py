"""Native synthetic cash trace and independent cash/quantity reconciliation."""

import hashlib
import json
from math import fsum
from pathlib import Path
from unittest.mock import patch

from test_settlement import LocalTrade, Trade, account, at, fill, trade, wallet


HERE = Path(__file__).resolve().parent


def main():
    old = Trade.use_db
    Trade.use_db = False
    LocalTrade.reset_trades()
    snapshots = []
    try:
        with patch.object(Trade, "commit"):
            w = wallet()
            a = account(w)

            def capture(label):
                orders = [
                    dict(
                        pair=t.pair,
                        order_id=o.order_id,
                        side=o.ft_order_side,
                        quantity=o.safe_filled,
                        price=o.safe_price,
                        fee_rate=t.fee_open if o.ft_order_side == "buy" else t.fee_close,
                    )
                    for t in Trade.get_trades_proxy()
                    for o in t.orders
                    if o.safe_filled
                ]
                cash = 1000 + fsum(
                    o["quantity"] * o["price"] * (1 - o["fee_rate"])
                    if o["side"] == "sell"
                    else -o["quantity"] * o["price"] * (1 + o["fee_rate"])
                    for o in orders
                )
                assert abs(w.get_total("USD") - cash) < 1e-9
                assert abs(w.get_free("USD") + w.get_used("USD") - cash) < 1e-9
                positions = {t.pair: t.amount for t in Trade.get_trades_proxy(is_open=True)}
                for pair, actual in positions.items():
                    expected = fsum(
                        o["quantity"] * (1 if o["side"] == "buy" else -1)
                        for o in orders
                        if o["pair"] == pair
                    )
                    assert abs(expected - actual) < 1e-9
                snapshots.append(
                    dict(
                        stage=label,
                        asof=w.asof.isoformat(),
                        orders=orders,
                        quote_cash=w.get_total("USD"),
                        free=w.get_free("USD"),
                        restricted=w.get_used("USD"),
                        holdings=positions,
                        entry_fee_reserve=w.cash_restriction.open_entry_fee_reserve,
                        native_closed_profit=LocalTrade.bt_total_profit,
                    )
                )

            capture("initial")
            t = trade()
            fill(a, t, at(2), 5, 100)
            capture("buy_with_fee_reserved")
            fill(a, t, at(3), 2, 110, False)
            capture("partial_exit_unsettled")
            w.advance(at(6))
            capture("next_session_before_release")
            t2 = trade("XYZ/USD", at(6))
            fill(a, t2, at(6), 1, 50)
            capture("second_buy_using_settled_cash_only")
            fill(a, t, at(6, 16), 3, 90, False)
            capture("full_exit_two_claims")
            w.advance(at(6, 22))
            capture("first_sale_released")
            w.advance(at(7, 22))
            capture("second_sale_released")
        result = dict(
            synthetic=True,
            strategy_return_evidence=False,
            historical_account_count=0,
            native_order_count=4,
            snapshots=snapshots,
            cash_and_quantity_reconciled=True,
            source_hashes={
                str(HERE / "test_settlement.py"): hashlib.sha256(
                    (HERE / "test_settlement.py").read_bytes()
                ).hexdigest()
            },
        )
        (HERE / "native_synthetic_trace.json").write_text(json.dumps(result, indent=2))
        print("4 native fills / 8 synthetic snapshots: cash and quantity reconciled")
    finally:
        LocalTrade.reset_trades()
        Trade.use_db = old


if __name__ == "__main__":
    main()
