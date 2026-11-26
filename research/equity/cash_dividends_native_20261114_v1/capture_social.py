"""Capture author-linked social leads without asserting video/transcript access."""
import hashlib,json,re
from datetime import UTC,datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import requests
h=Path(__file__).resolve().parent
urls={
 'x_dividends.html':'https://x.com/TheIdeaFarm/status/2048024226947649871?s=20',
 'youtube_dividends.html':'https://www.youtube.com/watch?v=PXaE8Bo06Ps',
 'show_notes.html':'https://www.themebfabershow.com/episodes/E2rpGXk3LrY',
}
def capture(item):
 name,url=item;row={'name':name,'url':url,'captured_utc':datetime.now(UTC).isoformat()}
 try:
  r=requests.get(url,timeout=25);p=h/'sources'/name;p.write_bytes(r.content)
  row.update(path=str(p),status=r.status_code,bytes=len(r.content),sha256=hashlib.sha256(r.content).hexdigest())
  if name.startswith('youtube'):
   marker='ytInitialPlayerResponse = ';pos=r.text.find(marker)
   if pos>=0:
    obj,end=json.JSONDecoder().raw_decode(r.text[pos+len(marker):])
    tracks=obj.get('captions',{}).get('playerCaptionsTracklistRenderer',{}).get('captionTracks',[])
    meta={'videoDetails':obj.get('videoDetails',{}),'playabilityStatus':obj.get('playabilityStatus'),
     'caption_tracks':tracks,'video_watched':False,'full_transcript_read':False}
    (h/'youtube_metadata.json').write_text(json.dumps(meta,indent=2)+'\n')
    if tracks:
     x=requests.get(tracks[0]['baseUrl']+'&fmt=json3',timeout=20)
     out=h/'sources/youtube_captions.json';out.write_bytes(x.content)
     row['caption_request']={'url':x.url,'status':x.status_code,'bytes':len(x.content),
      'path':str(out),'sha256':hashlib.sha256(x.content).hexdigest()}
   else:row['metadata_parse']='marker not found; no claimed video read'
 except requests.RequestException as e:row['error']=str(e)
 return row
rows=list(ThreadPoolExecutor(max_workers=3).map(capture,urls.items()))
(h/'social_receipts.json').write_text(json.dumps(rows,indent=2)+'\n')
print(json.dumps([{k:v for k,v in row.items()if k in('name','status','bytes','error','metadata_parse','caption_request')}for row in rows]))
