"""Capture source bytes; no remote trading code or imported social skills run."""
import hashlib,json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC,datetime
from html.parser import HTMLParser
from pathlib import Path
import requests
HERE=Path(__file__).resolve().parent
class Visible(HTMLParser):
 def __init__(self):super().__init__();self.hidden=0;self.out=[]
 def handle_starttag(self,t,a):
  if t in('script','style'):self.hidden+=1
 def handle_endtag(self,t):
  if t in('script','style'):self.hidden=max(0,self.hidden-1)
 def handle_data(self,s):
  if not self.hidden and s.strip():self.out.append(s.strip())
urls={
 'source_study.html':'https://tradewize.io/blog/how-to-read-the-long-short-ratio',
 'binance_market_data.html':'https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data',
 'binance_public_readme.md':'https://raw.githubusercontent.com/binance/binance-public-data/master/README.md',
 'native_strategy.md':'https://raw.githubusercontent.com/freqtrade/freqtrade/c051e9065c5b677f03817dd1f83cb980af1ed684/docs/strategy-customization.md',
}
def get(item):
 name,url=item;r={'url':url,'retrieved_at':datetime.now(UTC).isoformat()}
 try:
  x=requests.get(url,timeout=25);p=HERE/'source'/name;p.write_bytes(x.content)
  r.update(status=x.status_code,bytes=len(x.content),path=str(p),sha256=hashlib.sha256(x.content).hexdigest())
  if name.endswith('.html'):
   v=Visible();v.feed(x.text);t=p.with_suffix('.visible.txt');t.write_text('\n'.join(v.out))
   r.update(visible_path=str(t),visible_sha256=hashlib.sha256(t.read_bytes()).hexdigest(),visible_lines=len(v.out))
 except requests.RequestException as exc:r['error']=str(exc)
 return r
if __name__=='__main__':
 rows=list(ThreadPoolExecutor(max_workers=4).map(get,urls.items()))
 (HERE/'source_receipts.json').write_text(json.dumps(rows,indent=2)+'\n')
 print(json.dumps([{k:v for k,v in r.items()if k in('url','status','bytes','error')}for r in rows]))
