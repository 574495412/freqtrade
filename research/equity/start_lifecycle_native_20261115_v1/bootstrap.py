"""Load unchanged parent owners, then the local synthetic lifecycle extension."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PARENT = HERE.parent / 'open_only_native_20261115_v1'
sys.path.append(str(PARENT))
spec = importlib.util.spec_from_file_location('open_bootstrap', PARENT / 'bootstrap.py')
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)


def boot():
    manifest = parent.boot()
    name = 'freqtrade.optimize.equity_fixture_lifecycle'
    spec = importlib.util.spec_from_file_location(name, ROOT / 'freqtrade/optimize/equity_fixture_lifecycle.py')
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return manifest
