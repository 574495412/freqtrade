"""Restore must preserve owned positions and never absorb external orders."""
# ruff: noqa: S101

import pytest

from deploy.runtime_recovery_20261111.preflight import account_proxies, compare_positions
from deploy.runtime_recovery_20261111.service import definition


def test_live_definitions_restart_failed_workers_and_use_existing_native_entrypoint():
    for market in ["spot", "futures"]:
        value = definition(market)
        assert value["KeepAlive"] == {"SuccessfulExit": False}
        assert value["ThrottleInterval"] == 60
        assert value["ProgramArguments"][-1] == market
        assert "manual" not in value["ProgramArguments"]
        assert value["Label"] == "io.freqtrade.live." + market


def test_account_reconciliation_uses_native_account_transport_on_both_hosts():
    assert account_proxies({"exchange": {"ccxt_config": {}}}) == {}
    assert account_proxies({"exchange": {}}) == {}
    proxy = "http://127.0.0.1:7897"
    assert account_proxies({"exchange": {"ccxt_config": {"httpsProxy": proxy}}}) == {
        "http": proxy,
        "https": proxy,
    }


def test_matching_live_short_reconciles_and_changed_position_blocks_restore():
    trades = [{"pair": "DOT/USDT:USDT", "amount": 8.8, "is_short": True}]
    assert compare_positions(
        "futures", trades, {"positions": [{"symbol": "DOTUSDT", "positionAmt": "-8.8000"}]}
    ) == {"DOTUSDT": "-8.8000"}
    with pytest.raises(ValueError, match="restore refused"):
        compare_positions("futures", trades, {"positions": []})


def test_locked_quote_for_external_spot_buy_is_preserved_not_adopted_as_position():
    assert (
        compare_positions(
            "spot", [], {"balances": [{"asset": "USDT", "free": "115.07", "locked": "384.93"}]}
        )
        == {}
    )
    with pytest.raises(ValueError, match="restore refused"):
        compare_positions(
            "spot", [], {"balances": [{"asset": "ETH", "free": "0.1974", "locked": "0"}]}
        )
