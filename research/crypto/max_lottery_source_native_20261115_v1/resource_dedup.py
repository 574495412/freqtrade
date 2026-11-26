"""Preserve every byte/path while sharing terminal research indicator caches.

Only the two explicitly completed old research accounts below are eligible.
No raw input, strategy, account result, ZIP or unique cache is deleted.
"""
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parent = ROOT / 'user_data/crypto_quant/nfi_original_20261027_v001'
    source, target = parent/'baseline_v001', parent/'B_cost8_v001'
    for directory in [source,target]:
        assert json.loads((directory/'completion.json').read_text())['native_run_completed']
    before = shutil.disk_usage(ROOT).free
    records, checked = [], 0
    for path in sorted((target/'lossless_cache/indicators').glob('*.pkl')):
        canonical = source/'lossless_cache/indicators'/path.name
        if not canonical.is_file(): continue
        a,b = canonical.stat(),path.stat()
        if a.st_dev != b.st_dev or a.st_size != b.st_size or a.st_ino == b.st_ino: continue
        checked += 1
        digest = sha(canonical)
        if digest != sha(path): continue
        assert canonical.read_bytes() == path.read_bytes()
        assert a.st_mtime_ns == canonical.stat().st_mtime_ns and b.st_mtime_ns == path.stat().st_mtime_ns
        tmp = path.with_name(path.name+'.max_resource_link')
        assert not tmp.exists()
        os.link(canonical,tmp)
        os.replace(tmp,path)
        assert sha(canonical) == sha(path) == digest
        records.append(dict(path=str(path),canonical=str(canonical),sha256=digest,bytes=b.st_size,
            original_mtime_ns=b.st_mtime_ns,canonical_mtime_ns=a.st_mtime_ns))
        if shutil.disk_usage(ROOT).free >= 4*1024**3: break
    receipt = dict(at=datetime.now(UTC).isoformat(),before_free=before,
        after_free=shutil.disk_usage(ROOT).free,checked=checked,hardlinked=len(records),
        logical_duplicate_bytes=sum(r['bytes']for r in records),records=records,
        paths_and_bytes_preserved=True,financial_rules_or_evidence_changed=False,
        minimum_worker_free_bytes=2*1024**3,resource_floor_lowered=False)
    output = HERE / ('resource_receipt_'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')+'.json')
    output.write_text(json.dumps(receipt,indent=2)+'\n')
    print('PRESERVED',len(records),'identical cache paths;free',receipt['after_free'])


if __name__ == '__main__':
    main()
