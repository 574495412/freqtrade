"""Load the pinned native tree and unchanged parent equity owners."""
import importlib.util
import sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
SOURCE=HERE.with_name('start_lifecycle_native_20261115_v1')
PARENT=HERE.with_name('open_only_native_20261115_v1')
sys.path.append(str(SOURCE))
spec=importlib.util.spec_from_file_location('net_parent_bootstrap',SOURCE/'bootstrap.py')
parent=importlib.util.module_from_spec(spec);spec.loader.exec_module(parent)
def boot():
    manifest=parent.boot()
    name='freqtrade.optimize.equity_net_result'
    spec=importlib.util.spec_from_file_location(name,ROOT/'freqtrade/optimize/equity_net_result.py')
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return manifest
