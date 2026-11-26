"""Read-only social metadata; never infer full video rules from a headline."""
import hashlib,json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC,datetime
from html.parser import HTMLParser
from pathlib import Path
import requests
HERE=Path(__file__).resolve().parent
class Text(HTMLParser):
 def __init__(self):super().__init__();self.hidden=0;self.parts=[]
 def handle_starttag(self,t,a):
  if t in ('script','style'):self.hidden+=1
 def handle_endtag(self,t):
  if t in ('script','style'):self.hidden=max(0,self.hidden-1)
 def handle_data(self,s):
  if not self.hidden and s.strip():self.parts.append(s.strip())
urls={'x_hmm.html':'https://x.com/RuujSs/status/2074503360208884204',
 'youtube_hmm.html':'https://www.youtube.com/watch?v=NQpAqaJrm7U',
 'douyin_prior_lead.html':'https://www.douyin.com/video/7603647454390603035'}
folder=HERE/'social';folder.mkdir(exist_ok=True)
def capture(item):
 name,url=item;record={'url':url,'captured_utc':datetime.now(UTC).isoformat()}
 try:
  r=requests.get(url,timeout=25);p=folder/name;p.write_bytes(r.content)
  record.update(path=str(p),status=r.status_code,bytes=len(r.content),sha256=hashlib.sha256(r.content).hexdigest())
  parser=Text();parser.feed(r.text);txt=p.with_suffix('.visible.txt');txt.write_text('\n'.join(parser.parts))
  record.update(visible_path=str(txt),visible_sha256=hashlib.sha256(txt.read_bytes()).hexdigest(),visible_lines=len(parser.parts))
  if name.startswith('youtube'):
   marker='ytInitialPlayerResponse = ';pos=r.text.find(marker)
   if pos>=0:
    obj,_=json.JSONDecoder().raw_decode(r.text[pos+len(marker):]);tracks=obj.get('captions',{}).get('playerCaptionsTracklistRenderer',{}).get('captionTracks',[])
    meta={'videoDetails':obj.get('videoDetails',{}),'caption_tracks':tracks,'video_watched':False,'full_transcript_read':False}
    (HERE/'youtube_metadata.json').write_text(json.dumps(meta,indent=2)+'\n')
    if tracks:
     cap=requests.get(tracks[0]['baseUrl']+'&fmt=json3',timeout=20);out=folder/'captions.json';out.write_bytes(cap.content)
     record['caption']={'status':cap.status_code,'bytes':len(cap.content),'path':str(out),'sha256':hashlib.sha256(cap.content).hexdigest()}
 except requests.RequestException as e:record['error']=str(e)
 return record
rows=list(ThreadPoolExecutor(max_workers=3).map(capture,urls.items()))
(HERE/'social_receipts.json').write_text(json.dumps(rows,indent=2)+'\n')
print(json.dumps([{k:v for k,v in r.items()if k in('url','status','bytes','visible_lines','caption','error')}for r in rows]))
