"""Bounded primary-source intake; no external strategy execution or author messages."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent


def main():
    out = HERE / 'sources'
    out.mkdir(exist_ok=False)
    receipts = []

    def get(name, url):
        try:
            r = requests.get(url, timeout=25)
            if len(r.content) > 4 * 1024**2:
                raise ValueError('Source exceeds explicit four MiB capture bound')
            p = out / name
            p.write_bytes(r.content)
            receipts.append(dict(url=url, final_url=r.url, path=str(p), status=r.status_code,
                bytes=len(r.content), sha256=hashlib.sha256(r.content).hexdigest(),
                captured_utc=datetime.now(UTC).isoformat()))
            return r
        except (requests.RequestException, ValueError) as exc:
            receipts.append(dict(url=url, error=str(exc)))
        finally:
            (HERE / 'source_receipts.json').write_text(json.dumps(receipts, indent=2)+'\n')
            print(name, receipts[-1].get('status','capture_failed'), flush=True)

    r = get('gist_metadata.json', 'https://api.github.com/gists/10a3fa9622fe1fbf3ea1d8e360130e1e')
    if r is not None and r.status_code == 200:
        metadata = r.json()
        pin = metadata['history'][0]['version']
        get('gist_pinned.json', 'https://api.github.com/gists/10a3fa9622fe1fbf3ea1d8e360130e1e/' + pin)
        for name, file in metadata['files'].items():
            if name == 'main.py': get('shareholder_main.py', file['raw_url'])
    pin = '78de2ccd1e8d1544bd69f1b0494493f56559ec12'
    get('ValuationRatios.cs', f'https://raw.githubusercontent.com/QuantConnect/Lean/{pin}/Common/Data/Fundamental/ValuationRatios.cs')
    get('author_blog.html', 'https://mebfaber.com/2025/01/20/shareholder-yield-audiobook-free/')
    get('youtube.html', 'https://www.youtube.com/watch?v=yYBTlwn3x6o')
    get('x.html', 'https://x.com/MebFaber/status/1874984744494272950')


if __name__ == '__main__':
    main()
