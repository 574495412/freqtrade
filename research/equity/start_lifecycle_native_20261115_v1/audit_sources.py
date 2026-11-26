"""Source semantics/readiness only, without computing an investment return."""
import csv
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(name, obj):
    (HERE / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False)+'\n')


def main():
    source = (HERE / 'sources/shareholder_main.py').read_text()
    assert all(s in source for s in ['self.dollarVolumeListSize = 200', 'self.selectionSize = 10',
        'f.ValuationRatios.TotalYield', 'EqualWeightingPortfolioConstructionModel()',
        'Insight.Price(security.Symbol, timedelta(28)', 'self.SetCash(100000)'])
    assert (HERE / 'gist_head_receipt.txt').read_text().split()[0] == '39a9dc65229d0e9c64653d5310320fec08627b04'
    vendor = (HERE / 'sources/ValuationRatios_generated.cs').read_text()
    assert 'public double TotalYield' in vendor and 'public double BuyBackYield' in vendor
    assert 'net repurchase of shares outstanding over the market capital' in vendor
    inputs_path = HERE.parent / 'high_recency_native_20261114_v1/inputs.json'
    inputs = json.loads(inputs_path.read_text())
    rows = []
    for record in inputs['records']:
        path = Path(record['source'])
        assert sha(path) == record['source_sha256']
        with path.open() as file:
            header = next(csv.reader(file))
        matches = [s for s in header if any(v in s.lower() for v in ('totalyield', 'buybackyield', 'morningstar'))]
        rows.append(dict(symbol=record['symbol'], path=str(path), sha256=record['source_sha256'],
                         header=header, source_vendor_columns=matches))
    assert len(rows) == 514 and not any(row['source_vendor_columns'] for row in rows)
    local = ROOT.parent / 'Vibe-Trading/agent/src/factors/zoo/core/equity_npy24m.py'
    assert 'shares / shares.rolling(24' in local.read_text()
    previous = HERE.parent / 'open_only_native_20261115_v1/source_intake.json'
    save('input_admission.json', dict(passed=True, inputs_manifest=str(inputs_path),
        inputs_manifest_sha256=sha(inputs_path), verified_price_feature_files=len(rows),
        files_with_source_vendor_columns=0, records=rows,
        local_related_definition=dict(path=str(local), sha256=sha(local),
            equivalence='24month share-level average is not the source vendor net-repurchase yield'),
        scope='These514existing price/feature CSVs only; not all workstation or external financial data',
        admitted=False, factor_analysis_calls=0, financial_accounts=0))
    caption = json.loads((HERE / 'caption_receipt.json').read_text())
    assert caption['status'] == 200 and caption['bytes'] == 0
    save('source_intake.json', dict(
        github=dict(url='https://gist.github.com/ipl31/10a3fa9622fe1fbf3ea1d8e360130e1e',
            commit='39a9dc65229d0e9c64653d5310320fec08627b04', full_main_read=True,
            source_is_quantconnect_official=False, original_code_executed=False,
            license='No license grant in the reviewed file; no code adopted',
            rule='Monthly coarse top200 dollar volume; fine top10 vendor TotalYield; equal weights; Up/Flat28day insights on membership change',
            warning='Monthly selection and28day insight expiry are distinct; no reconstructed cash return',
            result='Educational source, no complete fixed author account artifacts or local vendor history'),
        field_definition=dict(url='https://github.com/QuantConnect/Lean/blob/78de2ccd1e8d1544bd69f1b0494493f56559ec12/Common/Data/Fundamental/Generated/ValuationRatios.cs',
            scope='Read BuyBackYield/TotalYield definition and adjacent fields, not whole class',
            definition='TotalYield = DividendYield + vendor net BuyBackYield; no debt-reduction term',
            limitation='Field definition is not a vintage vendor history or historical release certificate'),
        youtube=dict(url='https://www.youtube.com/watch?v=yYBTlwn3x6o',
            state='Actual title/author/description and caption tracks read; English caption HTTP200 zero bytes',
            watched=False, full_transcript_read=False, admitted=False),
        x=dict(url='https://x.com/MebFaber/status/1874984744494272950',
            state='DirectHTTP200 visible text read; web tool403; general thread with Shareholder Yield reply, no complete algorithm or account',
            admitted=False),
        author_blog=dict(url='https://mebfaber.com/2025/01/20/shareholder-yield-audiobook-free/',
            state='DirectHTTP403 preserved; web tool read short audiobook announcement; book/video not read or watched'),
        douyin=dict(state='New searches yielded no verified complete strategy rules; previous blocked source reused explicitly',
            parent_intake=str(previous), parent_intake_sha256=sha(previous),
            full_video_watched=False, admitted=False),
        new_accepted_alphas=0, new_registry_factors=0, new_factor_analysis_calls=0,
        historical_stock_accounts=0, combinations=0, author_returns_admitted=False))
    print('AUDITED source semantics;514 original input hashes/headers;0source vendor histories;0newfinancial accounts')


if __name__ == '__main__':
    main()
