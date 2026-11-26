"""Full native economics parity against immutable successful v1 leg paths."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

HERE=Path(__file__).resolve().parent


def stats(directory,strategy):
    paths=list((directory/'native_results').glob('*.zip'));assert len(paths)==1
    with ZipFile(paths[0])as archive:
        file=next(n for n in archive.namelist()if n.endswith('.json')and not n.endswith('_config.json'))
        return json.loads(archive.read(file))['strategy'][strategy]


def verify(*,all_successful=False):
    current=json.loads((HERE/'plan.json').read_text());old=HERE.with_name('paired_carry_source_native_20261115_v1')
    previous=json.loads((old/'plan.json').read_text());rows=[]
    for c in current['candidates']:
        if not all_successful and c['portfolio_capital']!=1000:continue
        name=c['name']+f"_{c['cost_bps']}bps"
        a=Path(previous['output_root'])/name;b=Path(current['output_root'])/name
        if not(a/'process_receipt.json').exists():continue
        receipt=json.loads((a/'process_receipt.json').read_text())
        if receipt['returncode']!=0:continue
        for directory in [a,b]:
            r=json.loads((directory/'process_receipt.json').read_text())
            assert r['returncode']==0 and hashlib.sha256((directory/'result.json').read_bytes()).hexdigest()==r['result_sha256']
        left,right=stats(a,c['strategy']),stats(b,c['strategy'])
        assert left['trades']==right['trades'],name
        for key in ['final_balance','profit_total_abs','total_trades']:
            assert left[key]==right[key],(name,key)
        x=pd.read_parquet(a/'hourly_equity.parquet');y=pd.read_parquet(b/'hourly_equity.parquet')
        pd.testing.assert_frame_equal(x,y,check_exact=True)
        rows.append(dict(candidate=c['name'],actual_native_trades=len(left['trades']),
            filled_orders=sum(len(t['orders'])for t in left['trades']),hourly_points=len(x),passed=True))
    assert len(rows)==(17 if all_successful else 12)
    result=dict(passed=True,complete_native_paths=len(rows),records=rows,
        all_order_fields_fees_funding_and_hourly_nav_exact=True,new_independent_economic_samples=0)
    name='full_parent_parity.json'if all_successful else'pre500_parity.json'
    (HERE/name).write_text(json.dumps(result,indent=2)+'\n')
    return result
