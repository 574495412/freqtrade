"""Linux preparation preserves the live financial and single-worker contracts."""
# ruff: noqa: S101

from pathlib import Path

import pytest

from deploy.aliyun_20261111.prepare import linux_config, unit


def test_linux_relocation_preserves_financial_contract_and_input():
    config = {
        "dry_run": False,
        "initial_state": "stopped",
        "available_capital": 99,
        "capital_ceiling_usdt": 100,
        "strategy": "NFIManualFlatBoost",
        "margin_mode": "isolated",
        "nfi_parameters": {"leverage": 3},
        "max_open_trades": 12,
        "daily_snapshot_file": "/old/user_data/pairs.json",
        "pairlists": [{"pairlist_url": "file:///old/user_data/pairs.json"}],
        "exchange": {
            "ccxt_config": {"httpsProxy": "http://127.0.0.1:7897"},
            "ccxt_async_config": {"httpsProxy": "http://127.0.0.1:7897"},
        },
    }
    result = linux_config(config, Path("/old"), Path("/opt/freqtrade"))
    assert result["pairlists"][0]["pairlist_url"] == "file:///opt/freqtrade/user_data/pairs.json"
    assert result["daily_snapshot_file"] == "/opt/freqtrade/user_data/pairs.json"
    assert result["initial_state"] == "stopped"
    for key in (
        "available_capital",
        "capital_ceiling_usdt",
        "margin_mode",
        "nfi_parameters",
        "max_open_trades",
    ):
        assert result[key] == config[key]
    assert result["exchange"]["ccxt_config"] == {}
    assert config["exchange"]["ccxt_config"]["httpsProxy"]


@pytest.mark.parametrize(
    "change", [{"dry_run": True}, {"available_capital": 495}, {"initial_state": "running"}]
)
def test_other_account_contract_refuses_preparation(change):
    config = {
        "dry_run": False,
        "initial_state": "stopped",
        "available_capital": 99,
        "capital_ceiling_usdt": 100,
        "strategy": "NFIManualFlatBoost",
    }
    with pytest.raises(ValueError):
        linux_config({**config, **change})


def test_systemd_uses_original_locked_worker_and_failure_restart():
    service = unit("futures", "deploy.mainnet_100_20261109.manual", "--worker futures")
    assert "-m deploy.mainnet_100_20261109.manual --worker futures" in service
    assert "User=freqtrade" in service and "Restart=on-failure" in service
    assert "UMask=0077" in service
    assert "ExecStartPost=" not in service
