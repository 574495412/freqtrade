"""Freeze an input-routing study and preserve bounded public source captures."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(name, value):
    (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def main():
    if (HERE / 'protocol.json').exists():
        raise FileExistsError('Never replace a frozen study')
    parent = HERE.parent / 'high_recency_native_20261114_v1'
    local = list((ROOT / 'freqtrade').glob('*equity*.py')) + list((ROOT / 'freqtrade/data').glob('equity*.py')) + list((ROOT / 'freqtrade/optimize').glob('equity*.py')) + [ROOT / 'freqtrade/data/history/equity.py']
    save('protocol.json', dict(
        frozen_utc=datetime.now(UTC).isoformat(), scope='HK/US daily input/session routing only',
        account_target_window=['2025-01-01', '2026-08-31'], original_universe='503 US + 11 HK; no result-based removals',
        parent_inputs=str(parent / 'inputs.json'), parent_inputs_sha256=sha(parent / 'inputs.json'),
        parent_admission=str(parent / 'native_admission.json'), parent_admission_sha256=sha(parent / 'native_admission.json'),
        signal='technical custody flags only: previous completed close > open; not an alpha or financial candidate',
        clocks='lag full-grid signals once; map target session labels to exact software-calendar opens; callback cache keeps session labels and completed-close visibility',
        financial_admission=False, full_account_runs=0, no_parameter_search=True,
        native_commit='c051e9065c5b677f03817dd1f83cb980af1ed684',
        local_components={str(p): sha(p) for p in local},
        no_orders_deployment_services=True,
        unresolved=['full native initialization/load/backtest/result lifecycle', 'daily fill chronology and closing auctions', 'historical execution/fee/dividend coverage', 'nominal quote units, actions and delistings', 'borrow and paired-leg funding for short strategies'],
    ))
    out = HERE / 'sources'
    out.mkdir()
    pin = '022ad81546b65c4955a1745bb5b8d0a4dc2429ab'
    urls = [
        ('overnight_README.md', f'https://raw.githubusercontent.com/DediGadot/overnight-anomaly/{pin}/README.md'),
        ('overnight_methodology.md', f'https://raw.githubusercontent.com/DediGadot/overnight-anomaly/{pin}/docs/methodology.md'),
        ('overnight_tree.json', f'https://api.github.com/repos/DediGadot/overnight-anomaly/git/trees/{pin}?recursive=1'),
        ('quantconnect_overnight.html', 'https://www.quantconnect.com/research/15296/overnight-anomaly/'),
        ('x_overnight.html', 'https://x.com/WheelieInvestor/status/2090827673136472542'),
        ('pollinate_regimes.html', 'https://pollinatetrading.mykajabi.com/blog/swing-trading-market-regimes'),
        ('youtube_regimes.html', 'https://www.youtube.com/watch?v=NKS8LcUSAF8'),
        ('youtube_overnight.html', 'https://www.youtube.com/watch?v=Ie-fKSvpA2M'),
        ('douyin_overnight.html', 'https://www.douyin.com/video/7671053326729547110'),
    ]
    receipts = []
    for name, url in urls:
        try:
            response = requests.get(url, timeout=30)
            path = out / name
            path.write_bytes(response.content)
            receipts.append(dict(url=url, path=str(path), status=response.status_code,
                                 sha256=sha(path), bytes=path.stat().st_size))
        except requests.RequestException as exc:
            receipts.append(dict(url=url, error=str(exc)))
        save('source_receipts.json', receipts)
        print(name, receipts[-1].get('status', 'request_error'), flush=True)


if __name__ == '__main__':
    main()
