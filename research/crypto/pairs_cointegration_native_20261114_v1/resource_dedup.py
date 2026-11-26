"""Preserve every byte/path while hard-linking identical historical cache files."""
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import psutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(4*1024**2),b''):h.update(chunk)
    return h.hexdigest()


def main():
    old=ROOT/'user_data/crypto_quant/nfi_original_20261027_v001'
    a=old/'A_original_observed_v002/lossless_cache'
    b=old/'A_original_observed_v003/lossless_cache'
    for p in psutil.process_iter(['pid','name','cmdline']):
        if p.pid==os.getpid() or not p.info['name'].lower().startswith(('python','freqtrade')):continue
        if any(str(old) in str(arg) for arg in (p.info['cmdline'] or [])):
            raise RuntimeError('Historical cache may be used by a live process')
    before=shutil.disk_usage(ROOT).free;records=[]
    for target in sorted(b.rglob('*')):
        if not target.is_file() or target.is_symlink():continue
        source=a/target.relative_to(b)
        if not source.is_file() or source.is_symlink():continue
        s,t=source.stat(),target.stat()
        if s.st_size!=t.st_size or (s.st_dev,s.st_ino)==(t.st_dev,t.st_ino):continue
        digest=sha(source)
        if sha(target)!=digest:continue
        temporary=target.with_name(target.name+'.exact_link_pending')
        if temporary.exists():raise RuntimeError('Unexpected pending cache link')
        os.link(source,temporary);os.replace(temporary,target)
        assert source.stat().st_ino==target.stat().st_ino and sha(target)==digest
        records.append(dict(source=str(source),target=str(target),bytes=t.st_size,sha256=digest))
        if shutil.disk_usage(ROOT).free>=3*1024**3:break
    result=dict(utc=datetime.now(UTC).isoformat(),before_free=before,
                after_free=shutil.disk_usage(ROOT).free,paths_and_bytes_preserved=True,
                records=records,source_failures_deleted=False)
    (HERE/'resource_dedup_receipt.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:result[k] for k in ['before_free','after_free','paths_and_bytes_preserved']}),flush=True)


if __name__=='__main__':main()
