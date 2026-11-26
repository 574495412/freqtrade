"""Continue only residual byte-identical dedup of two frozen historical caches.

The previous dedup intentionally stopped at its free-space target. Its sealed
receipt is never rewritten. No file path or content is deleted or changed.
"""
import hashlib
import json
import os
import shutil
from datetime import UTC,datetime
from pathlib import Path

import psutil

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024**2),b''):h.update(block)
    return h.hexdigest()


def main():
    old=ROOT/'user_data/crypto_quant/nfi_original_20261027_v001'
    a=old/'A_original_observed_v002/lossless_cache';b=old/'A_original_observed_v003/lossless_cache'
    for process in psutil.process_iter(['name','cmdline']):
        if process.pid==os.getpid() or not (process.info['name'] or '').lower().startswith(('python','freqtrade')):continue
        if any(str(old) in str(x) for x in (process.info['cmdline'] or [])):
            raise RuntimeError('Historical cache has a live process reference; preserve it')
    before=shutil.disk_usage(ROOT).free;records=[]
    for target in sorted(b.rglob('*')):
        if not target.is_file() or target.is_symlink():continue
        source=a/target.relative_to(b)
        if not source.is_file() or source.is_symlink():continue
        s,t=source.stat(),target.stat()
        if s.st_size!=t.st_size or (s.st_dev,s.st_ino)==(t.st_dev,t.st_ino):continue
        digest=sha(source)
        if sha(target)!=digest:continue
        temporary=target.with_name(target.name+'.freqai_residual_link')
        assert not temporary.exists();os.link(source,temporary);os.replace(temporary,target)
        assert sha(target)==digest and target.stat().st_ino==source.stat().st_ino
        records.append(dict(source=str(source),target=str(target),sha256=digest,bytes=t.st_size))
        if shutil.disk_usage(ROOT).free>=3*1024**3:break
    result=dict(recorded_utc=datetime.now(UTC).isoformat(),before_free_bytes=before,after_free_bytes=shutil.disk_usage(ROOT).free,
                records=records,all_paths_and_bytes_preserved=True,previous_receipts_unchanged=True,services_changed=False)
    destination=HERE/'historical_space_recovery.json';assert not destination.exists()
    destination.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(before=before,after=result['after_free_bytes'],residual_files_linked=len(records))))


if __name__=='__main__':main()
