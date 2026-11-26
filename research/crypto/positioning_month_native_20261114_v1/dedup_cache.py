"""Lossless hardlinks for byte-identical cache files of terminal owned runs only."""
import hashlib,json,os
from pathlib import Path

def dedup(root):
    root=Path(root);seen={};rows=[]
    for receipt in sorted(root.glob('*/process_receipt.json')):
        if json.loads(receipt.read_text())['returncode']!=0:continue
        for path in sorted((receipt.parent/'lossless_cache').rglob('*')):
            if not path.is_file()or path.is_symlink():continue
            digest=hashlib.sha256(path.read_bytes()).hexdigest();key=(path.stat().st_size,digest)
            if key not in seen:seen[key]=path;continue
            original=seen[key]
            if original.stat().st_ino==path.stat().st_ino:continue
            temporary=path.with_name(path.name+'.dedup-link')
            os.link(original,temporary);os.replace(temporary,path)
            assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
            rows.append({'path':str(path),'hardlink_source':str(original),'sha256':digest,'bytes_avoided':key[0]})
    return rows
if __name__=='__main__':
    h=Path(__file__).resolve().parent;root=h.parents[2]/'user_data/crypto_quant/geometric_hmm_native_20261114_v1'
    rows=dedup(root)
    (h/'prior_completed_cache_dedup.json').write_text(json.dumps({'scope':'only completed owned HMM cache files, all bytes preserved','records':rows,'bytes_avoided':sum(r['bytes_avoided']for r in rows)},indent=2)+'\n')
    print('Hardlinked identical completed cache files',len(rows),'bytes',sum(r['bytes_avoided']for r in rows))
