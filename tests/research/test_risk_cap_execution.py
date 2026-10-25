"""Native stop-market routing and funding ownership without an exchange connection."""

from types import SimpleNamespace

import ccxt
import pytest

from freqtrade.enums import TradingMode
from freqtrade.exceptions import InvalidOrderException
from freqtrade.exchange.binance import Binance
from freqtrade.freqtradebot import FreqtradeBot
from research.crypto.audit_paper_funding import campaign_funding


@pytest.mark.parametrize("side", ["sell", "buy"])
def test_native_stop_market_is_reduce_only_mark_triggered_and_has_no_limit(side):
    requests = []
    exchange = Binance.__new__(Binance)
    exchange.close = lambda: None
    exchange._config = {"dry_run": False}  # No CCXT client is created; the API below is local.
    exchange.trading_mode = TradingMode.FUTURES
    exchange._ft_has = {
        "stop_price_param": "stopPrice",
        **Binance._ft_has,
        **Binance._ft_has_futures,
    }
    exchange._params = {}
    exchange.price_to_precision = lambda pair, value, **kwargs: value
    exchange.amount_to_precision = lambda pair, value: value
    exchange._amount_to_contracts = lambda pair, value: value
    exchange._lev_prep = lambda *a, **k: None
    exchange._log_exchange_response = lambda *a, **k: None
    exchange._order_contracts_to_amount = lambda value: value

    def local_create(**kwargs):
        requests.append(kwargs)
        return {"id": "synthetic_stop_ack", **kwargs}

    exchange._api = SimpleNamespace(name="local_fixture", create_order=local_create)
    config = {"stoploss": "market", "stoploss_price_type": "mark", "stoploss_on_exchange": True}
    result = exchange.create_stoploss("ETH/USDT:USDT", 0.4, 88, config, side, 1)
    assert result["type"] == "stop_market"
    assert result["price"] is None
    assert result["params"]["reduceOnly"] is True
    assert result["params"]["workingType"] == "MARK_PRICE"
    assert result["params"]["stopPrice"] == 88
    assert len(requests) == 1

    def reject(**kwargs):
        raise ccxt.InvalidOrder("synthetic rejection")

    exchange._api.create_order = reject
    with pytest.raises(InvalidOrderException):
        exchange.create_stoploss("ETH/USDT:USDT", 0.4, 88, config, side, 1)


@pytest.mark.parametrize("short", [False, True])
def test_funding_uses_actual_remaining_quantity_after_partial_exit(short):
    trade = {"open_date": "2026-10-24T00:00:00Z", "is_short": short}
    orders = [
        {
            "filled": 2,
            "order_filled_date": "2026-10-24T00:01:00Z",
            "ft_order_side": "sell" if short else "buy",
        },
        {
            "filled": 1,
            "order_filled_date": "2026-10-24T04:00:00Z",
            "ft_order_side": "buy" if short else "sell",
        },
    ]
    event = {"fundingTime": 1792828800000, "fundingRate": "0.01", "markPrice": "100"}
    cash, count = campaign_funding(trade, orders, [event], event["fundingTime"])
    assert cash == (1 if short else -1)
    assert count == 1


def test_native_rejected_protection_routes_to_emergency_exit():
    attempts = []

    def reject(**kwargs):
        raise InvalidOrderException("synthetic stop rejection")

    trade = SimpleNamespace(pair="ETH/USDT:USDT", amount=0.4, exit_side="sell", leverage=1)
    worker = SimpleNamespace(
        exchange=SimpleNamespace(create_stoploss=reject),
        strategy=SimpleNamespace(order_types={"stoploss": "market"}),
        emergency_exit=lambda trade, price: attempts.append((trade, price)),
    )
    assert FreqtradeBot.create_stoploss_order(worker, trade, 88) is False
    assert attempts == [(trade, 88)]
