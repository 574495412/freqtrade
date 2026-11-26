"""Read-only captures; an HTTP success is not proof of readable rules."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
import requests
HERE=Path(__file__).resolve().parent
PIN='9f053398c4995651d822e90d81526d0644c6f3e5'
URLS={
 'DivSeason.py':f'https://raw.githubusercontent.com/OpenSourceAP/CrossSection/{PIN}/Signals/pyCode/Predictors/DivSeason.py',
 'LICENSE':f'https://raw.githubusercontent.com/OpenSourceAP/CrossSection/{PIN}/LICENSE',
 'sec_dividends.html':'https://www.investor.gov/introduction-investing/investing-basics/glossary/ex-dividend-dates-when-are-you-entitled-stock-and',
 'lean_corporate_actions.html':'https://www.quantconnect.com/docs/v2/writing-algorithms/securities/asset-classes/us-equity/corporate-actions',
 'douyin.html':'https://www.douyin.com/video/7562173571624127803',
 'native_backtesting.md':'https://raw.githubusercontent.com/freqtrade/freqtrade/c051e9065c5b677f03817dd1f83cb980af1ed684/docs/backtesting.md',
 'host_episode.html':'https://mebfaber.com/2026/04/24/sam-hartzmark/',
}
def capture(item):
 name,url=item;r={'name':name,'url':url,'captured_utc':datetime.now(UTC).isoformat()}
 try:
  x=requests.get(url,timeout=25);p=HERE/'sources'/name;p.write_bytes(x.content)
  r.update(status=x.status_code,path=str(p),bytes=len(x.content),sha256=hashlib.sha256(x.content).hexdigest(),final_url=x.url)
 except requests.RequestException as e:r['error']=str(e)
 return r
rows=list(ThreadPoolExecutor(max_workers=4).map(capture,URLS.items()))
(HERE/'source_receipts.json').write_text(json.dumps(rows,indent=2)+'\n')
print(json.dumps([{k:v for k,v in r.items()if k in ('name','status','bytes','error')}for r in rows]))
