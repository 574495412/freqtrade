"""Hardlink verified unchanged native OHLCV, marks and funding inputs."""
import hashlib,json,os
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
OUT=ROOT/'user_data/crypto_quant/positioning_month_native_20261114_v1'
parent=ROOT/'user_data/crypto_quant/tsmom_risk_native_20261114_v1/candles/futures'
(dest:=OUT/'candles/futures').mkdir(parents=True,exist_ok=True)
receipts={};rows=[]
for coin in ['BTC','ETH','SOL']:
 for suffix in ['1h-futures','5m-futures','1h-mark','1h-funding_rate']:
  name=f'{coin}_USDT_USDT-{suffix}.feather';src=parent/name;target=dest/name
  digest=hashlib.sha256(src.read_bytes()).hexdigest()
  if not target.exists():os.link(src,target)
  assert hashlib.sha256(target.read_bytes()).hexdigest()==digest
  receipts['futures/'+name]=digest;rows.append({'parent':str(src),'path':str(target),'sha256':digest})
(OUT/'data_receipts.json').write_text(json.dumps(receipts,indent=2)+'\n')
(HERE/'native_input_binding.json').write_text(json.dumps(rows,indent=2)+'\n')
print('Bound12nativeunchangedfiles by hardlink')
