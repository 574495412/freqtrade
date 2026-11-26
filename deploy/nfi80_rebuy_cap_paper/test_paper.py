# ruff: noqa: S101
"""Verify native order handling at the paper-only deployment boundary."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from deploy.nfi80_rebuy_cap_paper import paper
from deploy.nfi80_rebuy_cap_paper.strategy.NFIManualFlatBoostRebuyCap import (
    NFIManualFlatBoost,
    NFIManualFlatBoostRebuyCap,
)
from deploy.nfi80_rebuy_cap_paper.strategy.NFISelectedRebuyCapPaper import (
    NFISelectedFlatBoost,
    NFISelectedRebuyCapPaper,
)
from freqtrade.enums import RunMode, TradingMode
from freqtrade.persistence import Order, Trade


NOW = datetime(2026, 11, 26, tzinfo=UTC)


def entry(filled, *, open_order=False):
    return Order(
        ft_order_side="sell", ft_is_open=open_order, filled=filled,
        ft_amount=60, ft_price=1, status="open" if open_order else "closed", order_date=NOW,
    )


@pytest.mark.parametrize(
    ("orders", "proposed", "minimum", "eligible", "expected"),
    [
        ([entry(60)], (20.0, "rebuy"), 5, True, (20.0, "rebuy")),
        ([entry(60)], (20.1, "rebuy"), 5, True, None),
        ([entry(60), entry(15, open_order=True)], (18.0, "rebuy"), 5, True, None),
        ([entry(60), entry(15, open_order=True)], -5, 5, True, -5),
        ([entry(60), entry(57)], 0.5, 2, True, None),
        ([entry(60), entry(57)], (0.5, "rebuy"), 1, True, (0.5, "rebuy")),
        ([entry(60)], (21.0, "grind"), 5, False, (21.0, "grind")),
        ([], (2.0, "rebuy"), 1, True, None),
    ],
)
def test_cap_at_native_trade_boundary(monkeypatch, orders, proposed, minimum, eligible, expected):
    trade = Trade(
        pair="X/USDT:USDT", is_short=True, leverage=3,
        enter_tag="562" if eligible else "562 999", orders=orders,
        amount=60, open_rate=1, fee_open=0.0008,
    )
    monkeypatch.setattr(
        NFISelectedFlatBoost, "adjust_trade_position", lambda *args, **kwargs: proposed,
    )
    candidate = object.__new__(NFISelectedRebuyCapPaper)
    assert candidate.adjust_trade_position(trade, NOW, 1, -0.02, minimum, 100) == expected


def test_14_capacity_retains_12_way_stake(monkeypatch):
    candidate = object.__new__(NFISelectedRebuyCapPaper)
    wallet = Mock(return_value=83)
    candidate.wallets = SimpleNamespace(get_trade_stake_amount=wallet)
    sizing = Mock(return_value=103.75)
    monkeypatch.setattr(NFISelectedFlatBoost, "custom_stake_amount", sizing)
    received = candidate.custom_stake_amount("X/USDT:USDT", NOW, 1, 71, 5, 120, 3, "141", "long")
    wallet.assert_called_once_with("X/USDT:USDT", 12, update=False)
    sizing.assert_called_once_with("X/USDT:USDT", NOW, 1, 83, 5, 120, 3, "141", "long")
    assert received == 103.75


@pytest.mark.parametrize(
    ("mode", "is_dry", "slots"),
    [(RunMode.LIVE, False, 14), (RunMode.BACKTEST, True, 14), (RunMode.DRY_RUN, True, 12)],
)
def test_runtime_admits_only_intended_paper_account(mode, is_dry, slots):
    candidate = object.__new__(NFISelectedRebuyCapPaper)
    candidate.config = {
        "runmode": mode, "dry_run": is_dry, "trading_mode": TradingMode.FUTURES,
        "margin_mode": "isolated", "max_open_trades": slots, "stake_amount": "unlimited",
    }
    with pytest.raises(ValueError, match="futures paper"):
        candidate.bot_start()


def test_configuration_matches_local_pairlist_path_and_omits_integrations(tmp_path, monkeypatch):
    monkeypatch.setattr(paper, "ENGINE", paper.ROOT)
    snapshot = tmp_path / "current-pairs.json"
    monkeypatch.setattr(paper, "SNAPSHOT", snapshot)
    config = paper.configuration(NOW.isoformat())
    prefix = "file:///"
    local_path = config["pairlists"][0]["pairlist_url"].split(prefix, 1)[1]
    assert local_path == str(snapshot)
    assert not {"telegram", "api_server", "webhook"} & config.keys()
    assert config["dry_run"] and config["max_open_trades"] == 14


def test_live_candidate_shares_sizing_and_rebuy_with_paper():
    live, paper_class = NFIManualFlatBoostRebuyCap, NFISelectedRebuyCapPaper
    for name in ("adjust_trade_position", "custom_stake_amount"):
        assert getattr(live, name) is getattr(paper_class, name)
    for name in ("confirm_trade_entry", "analyze"):
        assert getattr(live, name) is getattr(NFIManualFlatBoost, name)


def test_live_candidate_preserves_parent_startup(monkeypatch):
    start = Mock()
    monkeypatch.setattr(NFIManualFlatBoost, "bot_start", start)
    candidate = object.__new__(NFIManualFlatBoostRebuyCap)
    candidate.config = {
        "dry_run": False, "runmode": RunMode.LIVE, "trading_mode": TradingMode.FUTURES,
        "margin_mode": "isolated", "max_open_trades": 14, "stake_amount": "unlimited",
    }
    candidate.bot_start()
    start.assert_called_once()
    candidate.config["max_open_trades"] = 12
    with pytest.raises(ValueError, match="14-slot isolated futures"):
        candidate.bot_start()
