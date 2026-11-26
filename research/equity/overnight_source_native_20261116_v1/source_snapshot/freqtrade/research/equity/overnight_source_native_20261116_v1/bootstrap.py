"""Pinned upstream engine plus explicit local history/clock extension."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VIBE = Path("/Users/chrise/GitHub/Vibe-Trading/agent")
ENGINE = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def boot():
    manifest = json.loads((ENGINE.parent / "FREQTRADE_SOURCE.json").read_text())
    if manifest["commit"] != "c051e9065c5b677f03817dd1f83cb980af1ed684":
        raise ValueError("Native engine version changed")
    for path, digest in manifest["files"].items():
        if sha(ENGINE / path) != digest:
            raise ValueError(f"Native source changed: {path}")
    sys.path.insert(0, str(ENGINE))
    sys.path.insert(0, str(VIBE))
    for name in [
        "freqtrade.data.history.equity",
        "freqtrade.data.equity_provider",
        "freqtrade.data.equity_overnight",
        "freqtrade.optimize.equity_backtesting",
    ]:
        spec = importlib.util.spec_from_file_location(name, ROOT / (name.replace(".", "/") + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return {"commit": manifest["commit"], "verified_files": len(manifest["files"])}
