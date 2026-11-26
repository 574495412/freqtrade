"""Verify the pinned native tree before loading the two explicit local owners."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).absolute().parents[3]
ENGINE=Path('/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')


def boot():
    source=json.loads((ENGINE.parent/'FREQTRADE_SOURCE.json').read_text())
    assert source['commit']=='c051e9065c5b677f03817dd1f83cb980af1ed684'
    for path,digest in source['files'].items():
        assert hashlib.sha256((ENGINE/path).read_bytes()).hexdigest()==digest
    sys.path.insert(0,str(ENGINE))
    for name in ['macro_transmission','macro_transmission_provider']:
        key='freqtrade.data.'+name
        spec=importlib.util.spec_from_file_location(key,ROOT/'freqtrade/data'/(name+'.py'))
        module=importlib.util.module_from_spec(spec);sys.modules[key]=module;spec.loader.exec_module(module)
    return dict(commit=source['commit'],original_native_files=len(source['files']))
