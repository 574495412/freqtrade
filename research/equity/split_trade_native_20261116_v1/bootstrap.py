"""Pinned upstream plus explicitly declared local native extension modules."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path


HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]
PARENT = HERE.with_name("open_only_native_20261115_v1")
SOURCE = HERE.with_name("dividend_lifecycle_native_20261116_v1")
sys.path.append(str(SOURCE))
spec = importlib.util.spec_from_file_location("split_parent_bootstrap", SOURCE / "bootstrap.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, default=str, allow_nan=False) + "\n")


def boot():
    manifest = parent.boot()
    for name in [
        "freqtrade.optimize.backtesting",
        "freqtrade.data.equity_splits",
        "freqtrade.persistence.equity_trade",
        "freqtrade.equity_split_wallets",
        "freqtrade.optimize.equity_split_execution",
    ]:
        spec = importlib.util.spec_from_file_location(name, ROOT / (name.replace(".", "/") + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return manifest
