"""Pin native code and load only the explicitly local earnings input owners."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
_spec = importlib.util.spec_from_file_location(
    'earnings_parent_boot', HERE.parent / 'high_recency_native_20261114_v1/bootstrap.py')
_parent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_parent)


def boot():
    result = _parent.boot()
    for name in ['equity_earnings', 'equity_earnings_provider']:
        module_name = 'freqtrade.data.' + name
        spec = importlib.util.spec_from_file_location(module_name, ROOT / 'freqtrade/data' / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return result
