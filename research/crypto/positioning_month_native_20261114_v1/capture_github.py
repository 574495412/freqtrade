"""Pin and read public native smart-money example, not replace its data source."""
import hashlib,json
from pathlib import Path
import requests
HERE=Path(__file__).resolve().parent;PIN='c6d9845330bf98ea9ba414a968faadbd9804735f'
rows=[]
for name in ['README.md','coinversaa_smart_money_strategy.py','coinversaa_client.py']:
 url=f'https://raw.githubusercontent.com/Coinversaa/coinversaa-freqtrade-example/{PIN}/{name}'
 r=requests.get(url,timeout=25);p=HERE/'source'/('coinversaa_'+name);p.write_bytes(r.content)
 rows.append({'repo':'Coinversaa/coinversaa-freqtrade-example','commit':PIN,'url':url,'path':str(p),
  'status':r.status_code,'bytes':len(r.content),'sha256':hashlib.sha256(r.content).hexdigest()})
(HERE/'github_receipts.json').write_text(json.dumps(rows,indent=2)+'\n');print([(r['status'],r['bytes'])for r in rows])
