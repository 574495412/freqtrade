"""Net-cash ROI targets under native fees, leverage, side and funding conventions."""

import sys
from pathlib import Path

import pytest

from freqtrade.enums import CandleType, TradingMode
from freqtrade.persistence import LocalTrade


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research/crypto/strategies"))
from inventory_recovery_risk import InventoryRiskTarget


@pytest.mark.parametrize(
    "short,stop,tag", [(False, 90, "small_inventory"), (True, 110, "core_inventory")]
)
@pytest.mark.parametrize("funding", [-0.5, 0.0, 0.5])
def test_target_matches_native_net_cash_without_crediting_positive_funding(
    short, stop, tag, funding
):
    strategy = InventoryRiskTarget({"candle_type_def": CandleType.FUTURES})
    trade = LocalTrade(
        pair="BTC/USDT:USDT",
        open_rate=100,
        amount=1,
        stake_amount=50,
        fee_open=0.0008,
        fee_close=0.0008,
        leverage=2,
        is_short=short,
        exchange="binance",
        funding_fees=funding,
        trading_mode=TradingMode.FUTURES,
    )
    trade.recalc_open_trade_value()
    trade.get_custom_data = lambda key: {"initial_stop": stop}
    roi = strategy.custom_roi(trade.pair, trade, None, 0, tag, "short" if short else "long")
    rate = trade.calc_close_rate_for_roi(roi)
    # Positive funding must not lower the planned loss used to set the target.
    actual_stop_loss = -trade.calculate_profit(stop).profit_abs
    planned_loss = actual_stop_loss + max(funding, 0)
    multiple = 1.5 if short else 0.75
    assert trade.calculate_profit(rate).profit_abs == pytest.approx(
        planned_loss * multiple, abs=2e-8
    )
