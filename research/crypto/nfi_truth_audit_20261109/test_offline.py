"""Historical metadata cannot be installed into a trading worker."""

import json
import socket
from pathlib import Path

import pytest

from freqtrade.enums import RunMode
from freqtrade.resolvers import ExchangeResolver
from research.crypto.nfi_truth_audit_20261109 import offline


@pytest.mark.parametrize(
    "dry_run,mode",
    [
        (False, RunMode.LIVE),
        (True, RunMode.DRY_RUN),
        (True, RunMode.LIVE),
        (False, RunMode.BACKTEST),
    ],
)
def test_research_adapter_rejects_trading_modes(tmp_path: Path, monkeypatch, dry_run, mode):
    snapshot = tmp_path / "metadata.json"
    snapshot.write_text(json.dumps({"markets": {}, "leverage_tiers": {}, "currencies": {}}))

    def must_not_load(*args, **kwargs):
        pytest.fail("Rejected mode reached native Exchange construction")

    monkeypatch.setattr(ExchangeResolver, "load_exchange", staticmethod(must_not_load))
    monkeypatch.setattr(socket.socket, "connect", socket.socket.connect)
    offline.install(snapshot)
    with pytest.raises(ValueError, match="exclusively for historical research"):
        ExchangeResolver.load_exchange({"dry_run": dry_run, "runmode": mode})
    with socket.socket() as connection:
        with pytest.raises(RuntimeError, match="outbound network access"):
            connection.connect(("127.0.0.1", 1))
