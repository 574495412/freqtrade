"""Pinned native implementation plus explicit local component modules."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ENGINE = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")


def boot():
    manifest = json.loads((ENGINE.parent / "FREQTRADE_SOURCE.json").read_text())
    assert manifest["commit"] == "c051e9065c5b677f03817dd1f83cb980af1ed684"
    for path, expected in manifest["files"].items():
        assert hashlib.sha256((ENGINE / path).read_bytes()).hexdigest() == expected, path
    sys.path.insert(0, str(ENGINE))
    for name, relative in [
        ("freqtrade.data.history.equity", "freqtrade/data/history/equity.py"),
        ("freqtrade.equity_wallets", "freqtrade/equity_wallets.py"),
        ("freqtrade.optimize.equity_backtesting", "freqtrade/optimize/equity_backtesting.py"),
        ("freqtrade.data.equity_execution", "freqtrade/data/equity_execution.py"),
        ("freqtrade.optimize.equity_execution", "freqtrade/optimize/equity_execution.py"),
        ("freqtrade.data.equity_fees", "freqtrade/data/equity_fees.py"),
        ("freqtrade.equity_fee_wallets", "freqtrade/equity_fee_wallets.py"),
        ("freqtrade.optimize.equity_fee_execution", "freqtrade/optimize/equity_fee_execution.py"),
        ("freqtrade.data.equity_dividends", "freqtrade/data/equity_dividends.py"),
        ("freqtrade.equity_dividend_wallets", "freqtrade/equity_dividend_wallets.py"),
        ("freqtrade.optimize.equity_nav", "freqtrade/optimize/equity_nav.py"),
    ]:
        spec = importlib.util.spec_from_file_location(name, ROOT / relative)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return manifest
