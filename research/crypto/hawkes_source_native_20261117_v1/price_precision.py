"""Extend the local native snapshot-tick interface for fixed source hawkes research.

Keeps the existing tick/clock implementation; changes only explicit admission of
the registered BTC/ETH futures backtest context. Does not edit sealed shared code.
"""

import hashlib
import importlib.util
from pathlib import Path

from ccxt import TICK_SIZE

from freqtrade.enums import RunMode, TradingMode
from freqtrade.persistence import Trade


BASE = Path("/Users/chrise/GitHub/freqtrade/freqtrade/optimize/matched_price_precision.py")
if (
    hashlib.sha256(BASE.read_bytes()).hexdigest()
    != "651d8e731c0a85649fb547ff27f5381eebb086e905537c27f8aee5b578aec9bb"
):
    raise ValueError("Shared snapshot precision implementation changed")
SPEC = importlib.util.spec_from_file_location("_sealed_matched_snapshot_precision", BASE)
OWNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OWNER)


class MatchedPricePrecisionMixin(OWNER.MatchedPricePrecisionMixin):
    def _require_matched_context(self):
        expected = ("BTC/USDT:USDT", "ETH/USDT:USDT")
        if (
            self.config.get("runmode") != RunMode.BACKTEST
            or self.config.get("dry_run") is not True
            or self.config.get("research_hawkes") is not True
            or Trade.use_db
            or self.trading_mode != TradingMode.FUTURES
            or self.trading_mode != self._matched_mode
            or self.exchange.precision_mode_price != TICK_SIZE
            or self._matched_pairs != expected
            or tuple(self.config["exchange"]["pair_whitelist"]) != expected
        ):
            raise ValueError("Declared fixed BTC/ETH futures hawkes backtest snapshot required")
