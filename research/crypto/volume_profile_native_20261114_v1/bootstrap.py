"""Pinned native tree and explicitly loaded local profile execution extension."""
import hashlib,importlib.util,json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
ENGINE=Path('/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')
def boot():
    manifest=json.loads((ENGINE.parent/'FREQTRADE_SOURCE.json').read_text())
    assert manifest['commit']=='c051e9065c5b677f03817dd1f83cb980af1ed684'
    for p,h in manifest['files'].items():assert hashlib.sha256((ENGINE/p).read_bytes()).hexdigest()==h,p
    sys.path.insert(0,str(ENGINE));sys.path.insert(0,str(HERE))
    name='freqtrade.optimize.volume_profile';path=ROOT/'freqtrade/optimize/volume_profile.py'
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m)
    return manifest
