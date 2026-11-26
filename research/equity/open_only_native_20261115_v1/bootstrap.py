"""Verify immutable native source, then explicitly load local extensions."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
parent = HERE.parent / 'session_routing_native_20261115_v1'
sys.path.append(str(parent))
spec = importlib.util.spec_from_file_location('session_bootstrap', parent / 'bootstrap.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def boot():
    manifest = module.boot()
    name = 'freqtrade.optimize.equity_open_only'
    spec = importlib.util.spec_from_file_location(name, ROOT / 'freqtrade/optimize/equity_open_only.py')
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return manifest
