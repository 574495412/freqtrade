"""Verify native pin and explicitly load the local feature extension."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
ENGINE=Path('/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')

def boot():
    manifest=json.loads((ENGINE.parent/'FREQTRADE_SOURCE.json').read_text())
    assert manifest['commit']=='c051e9065c5b677f03817dd1f83cb980af1ed684'
    for name,h in manifest['files'].items():
        assert hashlib.sha256((ENGINE/name).read_bytes()).hexdigest()==h,name
    sys.path.insert(0,str(ENGINE))
    name='freqtrade.data.residual_momentum'
    spec=importlib.util.spec_from_file_location(name,ROOT/'freqtrade/data/residual_momentum.py')
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return manifest
