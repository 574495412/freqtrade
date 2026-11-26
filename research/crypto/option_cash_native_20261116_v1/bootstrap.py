"""Pinned native tree and unchanged option input owners plus local cash owners."""

import importlib.util
import sys
from pathlib import Path


HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]
spec = importlib.util.spec_from_file_location(
    "option_input_boot", HERE.parent / "option_premium_admission_native_20261116_v1/bootstrap.py"
)
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)


def boot():
    result = parent.boot()
    for name, path in [
        ("freqtrade.data.option_cash", "data/option_cash.py"),
        ("freqtrade.option_wallets", "option_wallets.py"),
        ("freqtrade.optimize.option_fee_execution", "optimize/option_fee_execution.py"),
        ("freqtrade.optimize.option_fixture_lifecycle", "optimize/option_fixture_lifecycle.py"),
    ]:
        spec = importlib.util.spec_from_file_location(name, ROOT / "freqtrade" / path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return result
