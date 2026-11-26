"""Verify pinned Freqtrade and load only the explicitly local notice owners."""

import importlib.util
import sys
from pathlib import Path


HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]
spec = importlib.util.spec_from_file_location(
    "notice_parent_boot", HERE.parent / "high_recency_native_20261114_v1/bootstrap.py"
)
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)


def boot():
    native = parent.boot()
    for name in ("equity_split_notices", "equity_split_notices_provider"):
        fullname = "freqtrade.data." + name
        spec = importlib.util.spec_from_file_location(
            fullname, ROOT / "freqtrade/data" / (name + ".py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[fullname] = module
        spec.loader.exec_module(module)
    return native
