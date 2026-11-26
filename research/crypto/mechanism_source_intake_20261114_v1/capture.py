"""Version-pinned, bounded source intake; no external strategy execution."""
import requests,hashlib,json
from pathlib import Path
from datetime import UTC,datetime
HERE=Path(__file__).resolve().parent
JOBS=[('dominance','PeterLP123/systematic-crypto-research','a16b8c595fecbab40a25b7fdb30652c5bcda6b92',['README.md','LICENSE','strategy2_pipeline.py','strategy_helpers.py','systematic_crypto/signals.py','systematic_crypto/data.py']),('profile','VernonCM/bot-volume-profile-bybit','b213ff69f18bc0907249cbc8a0ea20146ccd199c',['README.md','config.py','indicadores.py','strategy.py','backtester.py'])]
def main():
 receipts=[]
 for tag,repo,commit,names in JOBS:
  for name in names:
   url=f'https://raw.githubusercontent.com/{repo}/{commit}/{name}';p=HERE/'sources'/tag/name;p.parent.mkdir(parents=True,exist_ok=True);assert not p.exists()
   r=requests.get(url,timeout=25);assert len(r.content)<2*1024**2;p.write_bytes(r.content)
   receipts.append({'repository':repo,'commit':commit,'url':url,'path':str(p),'status':r.status_code,'bytes':len(r.content),'sha256':hashlib.sha256(r.content).hexdigest(),'captured_utc':datetime.now(UTC).isoformat()});(HERE/'source_receipts.json').write_text(json.dumps(receipts,indent=2)+'\n');print(tag,name,r.status_code,len(r.content),flush=True)
if __name__=='__main__':main()
