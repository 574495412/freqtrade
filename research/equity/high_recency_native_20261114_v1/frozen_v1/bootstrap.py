"""Pin native engine, then load explicitly identified local extensions."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ENGINE = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")
MANIFEST = ENGINE.parent / "FREQTRADE_SOURCE.json"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def boot():
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["commit"] == "c051e9065c5b677f03817dd1f83cb980af1ed684"
    for path, expected in manifest["files"].items():
        assert sha(ENGINE / path) == expected, path
    sys.path.insert(0, str(ENGINE))
    for name, path in [
        ("freqtrade.data.history.equity", ROOT / "freqtrade/data/history/equity.py"),
        ("freqtrade.data.equity_provider", ROOT / "freqtrade/data/equity_provider.py"),
    ]:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return dict(
        commit=manifest["commit"],
        verified_files=len(manifest["files"]),
        manifest_sha256=sha(MANIFEST),
    )
