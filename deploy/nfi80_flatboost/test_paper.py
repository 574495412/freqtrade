# ruff: noqa: S101
"""Paper isolation and activation boundaries, without any exchange connection."""

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from deploy.nfi80_flatboost.launch import HERE, validate_paper


spec = importlib.util.spec_from_file_location(
    "paper_adapter", HERE / "strategy/NFIFlatTopBoostPaper.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize("mode,dry", [("live", False), ("live", True), ("dry_run", False)])
def test_strategy_refuses_live_or_nonpaper_account(mode, dry):
    strategy = object.__new__(module.NFIFlatTopBoostPaper)
    strategy.config = {"runmode": SimpleNamespace(value=mode), "dry_run": dry}
    with pytest.raises(ValueError, match="permits dry-run"):
        strategy.bot_start()


def test_common_start_blocks_only_prestart_entries_and_delegates_after(monkeypatch):
    strategy = object.__new__(module.NFIX8PaperControl)
    now = datetime.now(UTC)
    strategy.paper_entry_start = now + timedelta(minutes=1)
    delegated = []

    def original(self, *args, **kwargs):
        delegated.append(args)
        return "native-decision"

    monkeypatch.setattr(module.NostalgiaForInfinityX8, "confirm_trade_entry", original)
    args = ("BTC/USDT:USDT", "limit", 1, 10, "GTC")
    assert strategy.confirm_trade_entry(*args, now, "141", "long") is False
    assert not delegated
    assert (
        strategy.confirm_trade_entry(*args, strategy.paper_entry_start, "141", "long")
        == "native-decision"
    )
    strategy.paper_entry_start = None  # Historical replay uses the unchanged native decision.
    assert strategy.confirm_trade_entry(*args, now, "141", "long") == "native-decision"
    assert len(delegated) == 2


@pytest.mark.parametrize("role", ["optimized", "control"])
def test_launcher_rejects_credentials_and_wrong_contract(role):
    config = json.loads((HERE / f"{role}-config.json").read_text())
    validate_paper(config, role)
    for key, value in [("dry_run", False), ("max_open_trades", 6)]:
        changed = {**config, key: value}
        with pytest.raises(ValueError, match="contract"):
            validate_paper(changed, role)
    config["exchange"]["key"] = "dummy-test-key"
    with pytest.raises(ValueError, match="contract"):
        validate_paper(config, role)
