"""Verify pinned upstream bytes before loading the local cash extension."""

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
    for name, path in [
        ("freqtrade.data.history.equity", ROOT / "freqtrade/data/history/equity.py"),
        ("freqtrade.equity_wallets", ROOT / "freqtrade/equity_wallets.py"),
        (
            "freqtrade.optimize.equity_backtesting",
            ROOT / "freqtrade/optimize/equity_backtesting.py",
        ),
    ]:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return manifest
