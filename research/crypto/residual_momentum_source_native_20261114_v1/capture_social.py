"""Capture primary social page metadata; do not fetch or invent video content."""
import hashlib
import json
import re
from datetime import UTC,datetime
from html.parser import HTMLParser
from pathlib import Path

import requests

HERE=Path(__file__).resolve().parent
FOLDER=HERE/'social';FOLDER.mkdir(exist_ok=False)

class Visible(HTMLParser):
    def __init__(self):super().__init__();self.skip=0;self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.skip+=1
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.skip=max(0,self.skip-1)
    def handle_data(self,data):
        if not self.skip and data.strip():self.parts.append(data.strip())

def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

rows=[]
for name,url in [
    ('x_residual.html','https://x.com/0xArchiveIO/status/2114782927787831346'),
    ('youtube_momentum.html','https://www.youtube.com/watch?v=YFRJ_9RVrz4'),
    ('douyin_factor.html','https://www.douyin.com/video/7569936922768870697?source=Googlebot-sdc'),
    ('douyin_index.html','https://www.douyin.com/shipin/7610251142169462836')]:
    row={'url':url,'captured_utc':datetime.now(UTC).isoformat()}
    try:
        response=requests.get(url,timeout=25);path=FOLDER/name;path.write_bytes(response.content)
        row.update(status=response.status_code,bytes=len(response.content),path=str(path),sha256=sha(path))
        parser=Visible();parser.feed(response.text);(FOLDER/(name+'.visible.txt')).write_text('\n'.join(parser.parts)+'\n')
        if name.startswith('youtube'):
            for match in re.finditer(r'ytInitialPlayerResponse\s*=\s*',response.text):
                try:player=json.JSONDecoder().raw_decode(response.text[match.end():])[0]
                except json.JSONDecodeError:continue
                video=player.get('videoDetails',{});captions=player.get('captions',{})
                save(FOLDER/'youtube_metadata.json',{'video':{k:video.get(k)for k in ['title','author','shortDescription']},'captions':captions,'video_watched':False})
                tracks=captions.get('playerCaptionsTracklistRenderer',{}).get('captionTracks',[])
                if tracks:
                    capurl=tracks[0]['baseUrl']+'&fmt=json3'
                    r=requests.get(capurl,timeout=25);cap=FOLDER/'captions.json';cap.write_bytes(r.content)
                    save(FOLDER/'caption_receipt.json',{'url':capurl,'status':r.status_code,'path':str(cap),'bytes':len(r.content),'sha256':sha(cap),'full_transcript_read':False})
                break
    except requests.RequestException as exc:row['error']=str(exc)
    rows.append(row);save(HERE/'social_receipts.json',rows)
    print(name,row.get('status',row.get('error')),row.get('bytes'),flush=True)
