"""Verify pinned upstream bytes and add study-only dependencies to this process."""

import hashlib
import json
import sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
ENGINE=Path('/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')


def boot():
    manifest=json.loads((ENGINE.parent/'FREQTRADE_SOURCE.json').read_text())
    assert manifest['commit']=='c051e9065c5b677f03817dd1f83cb980af1ed684'
    for name,digest in manifest['files'].items():
        assert hashlib.sha256((ENGINE/name).read_bytes()).hexdigest()==digest,name
    sys.path.insert(0,str(HERE/'dependencies'));sys.path.insert(0,str(ENGINE));sys.path.insert(0,str(HERE))
    return manifest
