"""Immutable parent fixture inputs; no new economic candidates."""
import hashlib
import json
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
PARENT=HERE.parent/'copula_fills_native_20261115_v4'
OUT=ROOT/'user_data/crypto_quant/copula_admission_native_20261115_v5'
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,obj):Path(path).write_text(json.dumps(obj,indent=2,default=str)+'\n')
