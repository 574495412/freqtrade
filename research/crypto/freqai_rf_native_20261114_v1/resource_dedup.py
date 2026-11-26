"""Hard-link byte-identical immutable artifacts; preserve every file and value."""
import hashlib
import json
import os
from datetime import UTC,datetime
from pathlib import Path

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
OUT=ROOT/'user_data/crypto_quant/freqai_rf_native_20261114_v1'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def dedup():
    roots=[]
    for receipt in OUT.rglob('process_receipt.json'):
        record=json.loads(receipt.read_text())
        if record['returncode']==0:
            assert sha(receipt.parent/'result.json')==record['result_sha256']
            roots.append(receipt.parent)
    if (HERE/'final_revalidation.json').exists() and json.loads((HERE/'final_revalidation.json').read_text())['passed']:
        roots.append(OUT/'components')
    if (HERE/'v002/preflight.json').exists() and json.loads((HERE/'v002/preflight.json').read_text())['passed']:
        roots.append(OUT/'v002/components')
    seen={};links=[];checked=0;freed=0
    for root in sorted(roots):
        for path in sorted(root.rglob('*')):
            if not path.is_file() or path.suffix not in {'.parquet','.npz','.feather'}:continue
            stat=path.stat();digest=sha(path);key=(stat.st_size,digest);checked+=1
            if key not in seen:seen[key]=path;continue
            source=seen[key];other=source.stat()
            if stat.st_dev==other.st_dev and stat.st_ino==other.st_ino:continue
            assert path.read_bytes()==source.read_bytes()
            temporary=path.with_name(path.name+'.dedup_tmp');assert not temporary.exists()
            os.link(source,temporary);os.replace(temporary,path)
            assert sha(path)==digest
            links.append(dict(path=str(path),canonical=str(source),sha256=digest,bytes=stat.st_size,
                              original_mtime_ns=stat.st_mtime_ns,canonical_mtime_ns=other.st_mtime_ns))
            freed+=stat.st_size
    result=dict(recorded_utc=datetime.now(UTC).isoformat(),files_checked=checked,
                byte_identical_paths_hardlinked=links,logical_duplicate_bytes=freed,
                paths_and_bytes_preserved=True,financial_config_or_strategy_changed=False)
    directory=HERE/'resource_receipts';directory.mkdir(exist_ok=True)
    (directory/f'{datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")}.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(immutable_files_checked=checked,paths_hardlinked=len(links),duplicate_bytes=freed)),flush=True)


if __name__=='__main__':dedup()
