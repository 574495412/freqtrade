"""Capture primary source bytes, with failed requests retained explicitly."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
import requests

HERE = Path(__file__).resolve().parent
PIN = '9f053398c4995651d822e90d81526d0644c6f3e5'
NATIVE = 'c051e9065c5b677f03817dd1f83cb980af1ed684'
URLS = {
    'CustomerMomentum.py': f'https://raw.githubusercontent.com/OpenSourceAP/CrossSection/{PIN}/Signals/pyCode/Predictors/CustomerMomentum.py',
    'LICENSE': f'https://raw.githubusercontent.com/OpenSourceAP/CrossSection/{PIN}/LICENSE',
    'cohen_2006_draft.pdf': 'https://w4.stern.nyu.edu/finance/docs/pdfs/Seminars/063f-cohen.pdf',
    'native_backtesting.md': f'https://raw.githubusercontent.com/freqtrade/freqtrade/{NATIVE}/docs/backtesting.md',
    'x_pairs.html': 'https://x.com/MatiasScalbi/status/2063042609816252666',
    'youtube.html': 'https://www.youtube.com/watch?v=j6K-pyF1hdI',
    'douyin.html': 'https://www.douyin.com/video/7668639927499918627',
}
records = []
for name, url in URLS.items():
    record = {'url': url, 'captured_utc': datetime.now(UTC).isoformat()}
    try:
        response = requests.get(url, timeout=25)
        path = HERE / 'sources' / name
        path.write_bytes(response.content)
        record.update(status=response.status_code, bytes=len(response.content), path=str(path),
                      sha256=hashlib.sha256(response.content).hexdigest())
    except requests.RequestException as exc:
        record['error'] = str(exc)
    records.append(record)
    (HERE / 'source_receipts.json').write_text(json.dumps(records, indent=2) + '\n')
    print(name, record.get('status', record.get('error')), record.get('bytes'), flush=True)
