"""Verify upstream then explicitly load local equity modules, without installation."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / 'cash_dividends_native_20261114_v1'))
spec = importlib.util.spec_from_file_location('cash_bootstrap', HERE.parent / 'cash_dividends_native_20261114_v1/bootstrap.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def boot():
    manifest = module.boot()
    for name, relative in [
        ('freqtrade.data.equity_provider', 'freqtrade/data/equity_provider.py'),
        ('freqtrade.optimize.equity_sessions', 'freqtrade/optimize/equity_sessions.py'),
    ]:
        spec = importlib.util.spec_from_file_location(name, ROOT / relative)
        loaded = importlib.util.module_from_spec(spec)
        sys.modules[name] = loaded
        spec.loader.exec_module(loaded)
    return manifest
