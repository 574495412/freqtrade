"""Pinned native code plus explicit local input extension."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
ENGINE=Path('/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')
def boot():
    manifest=json.loads((ENGINE.parent/'FREQTRADE_SOURCE.json').read_text())
    assert manifest['commit']=='c051e9065c5b677f03817dd1f83cb980af1ed684'
    for p,h in manifest['files'].items():assert hashlib.sha256((ENGINE/p).read_bytes()).hexdigest()==h,p
    sys.path.insert(0,str(ENGINE))
    name='freqtrade.data.signed_jump';spec=importlib.util.spec_from_file_location(name,ROOT/'freqtrade/data/signed_jump.py')
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return manifest
