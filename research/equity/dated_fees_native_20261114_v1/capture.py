"""Capture source bytes once; failed responses remain evidence, never rules."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
import requests

HERE = Path(__file__).resolve().parent
SOURCES = {
    'hkex_settlement_change.pdf': 'https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/HKSCC/2025/ce_HKSCC_SET_022_2025.pdf',
    'hkex_transaction.html': 'https://www.hkex.com.hk/Services/Rules-and-Forms-and-Fees/Fees/Securities-%28Hong-Kong%29/Trading/Transaction?sc_lang=en',
    'sec_2025.html': 'https://www.sec.gov/rules-regulations/fee-rate-advisories/2025-2',
    'sec_2026.html': 'https://www.sec.gov/rules-regulations/fee-rate-advisories/2026-2',
    'sec_obligation.html': 'https://www.sec.gov/rules-regulations/fee-rate-advisories/section-31-transaction-fees-basic-information-firms',
    'nasdaq_2026.html': 'https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2026-18',
    'Investment.py': 'https://raw.githubusercontent.com/OpenSourceAP/CrossSection/9f053398c4995651d822e90d81526d0644c6f3e5/Signals/pyCode/Predictors/Investment.py',
    'asrol.py': 'https://raw.githubusercontent.com/OpenSourceAP/CrossSection/9f053398c4995651d822e90d81526d0644c6f3e5/Signals/pyCode/utils/asrol.py',
    'CrossSection_LICENSE': 'https://raw.githubusercontent.com/OpenSourceAP/CrossSection/9f053398c4995651d822e90d81526d0644c6f3e5/LICENSE',
    'quantprogram_youtube.html': 'https://www.youtube.com/watch?v=j6K-pyF1hdI',
    'douyin_system.html': 'https://www.douyin.com/video/7668639927499918627',
    'x_pairs.html': 'https://x.com/MatiasScalbi/status/2063042609816252666',
}

def main():
    receipts=[]
    for name,url in SOURCES.items():
        path=HERE/'sources'/name
        assert not path.exists(), 'Preserve original capture'
        row={'url':url, 'captured_utc':datetime.now(UTC).isoformat()}
        try:
            r=requests.get(url,timeout=25)
            path.write_bytes(r.content)
            row.update(status=r.status_code, path=str(path), bytes=len(r.content),sha256=hashlib.sha256(r.content).hexdigest(),content_type=r.headers.get('Content-Type'))
        except requests.RequestException as e:
            row['failure']=type(e).__name__+': '+str(e)
        receipts.append(row)
        (HERE/'source_receipts.json').write_text(json.dumps(receipts,indent=2)+'\n')
        print(name,row.get('status'),row.get('bytes'),flush=True)
if __name__=='__main__': main()
