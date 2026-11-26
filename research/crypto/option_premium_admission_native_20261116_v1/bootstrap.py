"""Verify the upstream native tree,then load only explicit local option owners."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path


HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]
ENGINE = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")


def boot():
    manifest = json.loads((ENGINE.parent / "FREQTRADE_SOURCE.json").read_text())
    assert manifest["commit"] == "c051e9065c5b677f03817dd1f83cb980af1ed684"
    for path, expected in manifest["files"].items():
        assert hashlib.sha256((ENGINE / path).read_bytes()).hexdigest() == expected, path
    sys.path.insert(0, str(ENGINE))
    for name, relative in [
        ("freqtrade.data.inverse_option_contracts", "data/inverse_option_contracts.py"),
        ("freqtrade.data.history.inverse_options", "data/history/inverse_options.py"),
        ("freqtrade.optimize.option_backtesting", "optimize/option_backtesting.py"),
    ]:
        spec = importlib.util.spec_from_file_location(name, ROOT / "freqtrade" / relative)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return {"commit": manifest["commit"], "verified_files": len(manifest["files"])}
