"""Revalidate pinned native ownership, explicitly load the local FIP extension."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ENGINE = ROOT.parent / 'CZ-Agent/vendor/freqtrade'


def boot():
    manifest = json.loads((ENGINE.parent / 'FREQTRADE_SOURCE.json').read_text())
    assert manifest['commit'] == 'c051e9065c5b677f03817dd1f83cb980af1ed684'
    for path, digest in manifest['files'].items():
        assert hashlib.sha256((ENGINE / path).read_bytes()).hexdigest() == digest
    sys.path.insert(0, str(ENGINE))
    name = 'freqtrade.data.fip_path'
    spec = importlib.util.spec_from_file_location(name, ROOT / 'freqtrade/data/fip_path.py')
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return manifest
