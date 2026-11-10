# ruff: noqa: S101
"""Exercise installed CCXT discovery without any external traffic."""

import asyncio

import ccxt.async_support as ccxt
import pytest

from deploy.mainnet_100_20261109.spot_transport import scoped_config


def test_transport_change_preserves_every_financial_and_credential_field():
    original = {
        "trading_mode": "spot",
        "available_capital": 99,
        "capital_ceiling_usdt": 100,
        "max_open_trades": 6,
        "strategy": "NFIManualFlatBoost",
        "exchange": {
            "key": "dummy",
            "secret": "dummy",
            "pair_whitelist": ["BTC/USDT"],
            "ccxt_config": {"options": {"broker": {"spot": None}}},
        },
    }
    candidate = scoped_config(original)
    assert "fetchMarkets" not in original["exchange"]["ccxt_config"]["options"]
    restored = scoped_config(candidate)
    assert restored == candidate
    for name in ("ccxt_config", "ccxt_async_config"):
        options = candidate["exchange"][name]["options"]
        assert options["fetchMarkets"] == {"types": ["spot"], "loadAllOptions": False}
        assert options["fetchMargins"] is False
        del options["fetchMarkets"], options["fetchMargins"]
    del candidate["exchange"]["ccxt_async_config"]
    assert candidate == original


def run_discovery(options):
    async def run():
        ex = ccxt.binance({"options": options})
        calls = []

        async def spot(params):
            calls.append("spot")
            return {"symbols": []}

        async def blocked(params):
            calls.append("unneeded-futures-or-margin")
            raise ccxt.DDoSProtection("Cached fixture418; no real request")

        ex.publicGetExchangeInfo = spot
        ex.fapiPublicGetExchangeInfo = blocked
        ex.dapiPublicGetExchangeInfo = blocked
        ex.sapiGetMarginAllPairs = blocked
        ex.sapiGetMarginIsolatedAllPairs = blocked
        ex.check_required_credentials = lambda *args: True
        try:
            await ex.fetch_markets()
            return calls, False
        except ccxt.DDoSProtection:
            return calls, True
        finally:
            await ex.close()

    return asyncio.run(run())


def test_installed_ccxt_default_scope_fails_on_unneeded_derivative_fixture():
    calls, failed = run_discovery({})
    assert failed and "unneeded-futures-or-margin" in calls


def test_installed_ccxt_spot_scope_avoids_blocked_derivatives_and_margin():
    calls, failed = run_discovery(
        {"fetchMarkets": {"types": ["spot"], "loadAllOptions": False}, "fetchMargins": False}
    )
    assert not failed and calls == ["spot"]


@pytest.mark.parametrize(
    "field,value",
    [("trading_mode", "futures"), ("available_capital", 100), ("capital_ceiling_usdt", 1000)],
)
def test_wrong_market_or_budget_cannot_use_spot_change(field, value):
    cfg = {
        "trading_mode": "spot",
        "available_capital": 99,
        "capital_ceiling_usdt": 100,
        "exchange": {},
    }
    cfg[field] = value
    with pytest.raises(ValueError):
        scoped_config(cfg)
