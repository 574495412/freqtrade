"""Author metadata and source counterevidence, not a video-strategy reproduction."""
import hashlib,json
from datetime import UTC,datetime
from pathlib import Path
import requests
from capture import Visible
h=Path(__file__).resolve().parent
urls={
 'youtube_ratio.html':'https://www.youtube.com/watch?v=qrBeuJm0fD4',
 'janorium_counterevidence.html':'https://janorium.com/articles/long-short-ratio-open-interest-backtest',
 'douyin_prior_system.html':'https://www.douyin.com/video/7603647454390603035',
 'x_prior_regime.html':'https://x.com/RuujSs/status/2074503360208884204',
}
rows=[]
for name,url in urls.items():
 r={'url':url,'retrieved_at':datetime.now(UTC).isoformat()}
 try:
  x=requests.get(url,timeout=20);p=h/'source'/name;p.write_bytes(x.content)
  r.update(status=x.status_code,bytes=len(x.content),path=str(p),sha256=hashlib.sha256(x.content).hexdigest())
  v=Visible();v.feed(x.text);t=p.with_suffix('.visible.txt');t.write_text('\n'.join(v.out));r.update(visible_path=str(t),visible_sha256=hashlib.sha256(t.read_bytes()).hexdigest())
  if name.startswith('youtube'):
   marker='ytInitialPlayerResponse = ';pos=x.text.find(marker)
   if pos>=0:
    obj,_=json.JSONDecoder().raw_decode(x.text[pos+len(marker):]);tracks=obj.get('captions',{}).get('playerCaptionsTracklistRenderer',{}).get('captionTracks',[])
    (h/'youtube_metadata.json').write_text(json.dumps({'videoDetails':obj.get('videoDetails',{}),'caption_tracks':tracks,'video_watched':False,'full_transcript_read':False},indent=2)+'\n')
    if tracks:
     cap=requests.get(tracks[0]['baseUrl']+'&fmt=json3',timeout=20);cp=h/'source/captions.json';cp.write_bytes(cap.content)
     r['caption']={'path':str(cp),'status':cap.status_code,'bytes':len(cap.content),'sha256':hashlib.sha256(cap.content).hexdigest()}
 except requests.RequestException as exc:r['error']=str(exc)
 rows.append(r);(h/'social_receipts.json').write_text(json.dumps(rows,indent=2)+'\n')
 print(name,r.get('status'),r.get('bytes'),flush=True)
