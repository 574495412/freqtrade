"""External strategy valuation must account for changing inventory and partial exits."""

import ccxt
import pandas as pd
import pytest

from freqtrade.enums import TradingMode
from freqtrade.persistence import LocalTrade, Order
from research.crypto.prepare_nfi import complete
from research.crypto.run_nfi import open_trade_value


@pytest.mark.parametrize("is_short,expected", [(False, -2.16), (True, 1.44)])
def test_native_partial_exit_equity_counts_funding_once(is_short, expected):
    entry, exit_side = ("sell", "buy") if is_short else ("buy", "sell")
    direction = 1 if is_short else -1
    orders = [
        Order(
            ft_order_side=entry,
            ft_is_open=False,
            filled=1,
            amount=1,
            average=100,
            price=100,
            funding_fee=0,
        ),
        Order(
            ft_order_side=entry,
            ft_is_open=False,
            filled=1,
            amount=1,
            average=80,
            price=80,
            funding_fee=direction * 1,
        ),
        Order(
            ft_order_side=exit_side,
            ft_is_open=False,
            filled=1,
            amount=1,
            average=95,
            price=95,
            funding_fee=direction * 0.6,
        ),
    ]
    trade = LocalTrade(
        pair="BTC/USDT:USDT",
        amount=1,
        open_rate=90,
        is_short=is_short,
        fee_open=0.001,
        fee_close=0.001,
        trading_mode=TradingMode.FUTURES,
        leverage=3,
        amount_precision=8,
        precision_mode=ccxt.DECIMAL_PLACES,
        contract_size=1,
    )
    trade.orders = orders  # LocalTrade.__init__ deliberately creates its own empty order list.
    trade.recalc_trade_from_orders()
    trade.set_funding_fees(direction * 0.2)
    # Cashflow identity: total entry notional 180, partial exit 95, remaining mark 85.
    # Gross PnL is zero; all fees including a closing reserve are 0.36, funding is +/-1.8.
    assert trade.amount == 1
    assert open_trade_value(trade, 85) == pytest.approx(expected)
    assert trade.funding_fees == pytest.approx(direction * 1.8)  # Observation is read-only.


def test_five_minute_calendar_rejects_gaps_and_duplicates():
    frame = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=12, freq="5min", tz="UTC")})
    assert len(complete(frame, "2024-01-01", "2024-01-01T01:00", "5min")) == 12
    for broken in (frame.drop(index=3), pd.concat([frame, frame.iloc[[3]]])):
        with pytest.raises(ValueError, match="Missing or duplicate"):
            complete(broken, "2024-01-01", "2024-01-01T01:00", "5min")


def test_all_winning_native_pf_zero_is_not_mistaken_for_zero_payoff(tmp_path, monkeypatch):
    import research.crypto.audit_nfi as auditing
    from freqtrade.research.data import save

    result = {
        "net_profit_usdt": 31.0,
        "net_return": 0.031,
        "closed_trades": 31,
        "profit_factor": 0.0,
        "risk_drawdown_for_screening": 0.01,
        "phase": "train",
        "variant": "NFIReference3x",
        "extra_cost_bps_per_side": 3,
    }
    save(tmp_path / "result.json", result)
    save(
        tmp_path / "plan.json",
        {
            "screening_targets": {
                "minimum_closed_trades_per_evaluation": 30,
                "minimum_profit_factor": 1.1,
                "maximum_observed_drawdown": 0.15,
            }
        },
    )
    monkeypatch.setattr(auditing, "native_stats", lambda _: {"trades": [{"profit_abs": 1.0}] * 31})
    checked = auditing.audit(tmp_path)
    assert checked["profit_factor"] is None
    assert checked["profit_factor_status"] == "NO_LOSSES_OBSERVED"
    assert checked["phase_economic_screen_passed"] is True
    save(tmp_path / "result.json", dict(result, net_profit_usdt=32))
    with pytest.raises(ValueError, match="did not reconcile"):
        auditing.audit(tmp_path)
