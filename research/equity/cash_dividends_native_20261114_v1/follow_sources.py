"""Capture exact source helpers and an existing social lead; preserve failures."""
import hashlib
import json
from datetime import UTC,datetime
from pathlib import Path
import requests
from html.parser import HTMLParser
class Visible(HTMLParser):
 def __init__(self):super().__init__();self.hidden=0;self.parts=[]
 def handle_starttag(self,t,a):
  if t in ("script","style"):self.hidden+=1
 def handle_endtag(self,t):
  if t in ("script","style"):self.hidden=max(0,self.hidden-1)
 def handle_data(self,d):
  if not self.hidden and d.strip():self.parts.append(d.strip())
h=Path(__file__).resolve().parent
base='https://raw.githubusercontent.com/OpenSourceAP/CrossSection/9f053398c4995651d822e90d81526d0644c6f3e5/Signals/pyCode/utils/'
urls={n:base+n for n in ['asrol.py','stata_replication.py','save_standardized.py']}
urls['x_existing_pairs.html']='https://x.com/MatiasScalbi/status/2063042609816252666'
urls['host_actual_article.html']='https://mebfaber.com/2026/04/30/sam-hartzmark-on-dividends/'
rows=[]
for name,url in urls.items():
 row={'name':name,'url':url,'captured_utc':datetime.now(UTC).isoformat()}
 try:
  r=requests.get(url,timeout=20);p=h/'sources'/name;p.write_bytes(r.content)
  row.update(status=r.status_code,bytes=len(r.content),path=str(p),sha256=hashlib.sha256(r.content).hexdigest())
  if name.endswith('.html'):
   soup=Visible();soup.feed(r.text)
   txt='\n'.join(soup.parts);out=p.with_suffix('.visible.txt');out.write_text(txt)
   row['visible_text_path']=str(out);row['visible_text_sha256']=hashlib.sha256(out.read_bytes()).hexdigest()
   row['visible_characters']=len(txt)
 except requests.RequestException as e:row['error']=str(e)
 rows.append(row)
 (h/'follow_source_receipts.json').write_text(json.dumps(rows,indent=2)+'\n')
 print(name,row.get('status'),row.get('bytes'),flush=True)
