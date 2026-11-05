"""Independent Decimal terminal reconciliation and compact control receipts."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[4]
HERE=Path(__file__).resolve().parent

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def load(path):return json.loads(path.read_text())

def main():
    rows=[]
    for path in sorted((HERE/'manifests').glob('P1_B[01]_*.json')):
        manifest=load(path);out=ROOT/manifest['isolated_output_path'];result=load(out/'result.json')
        if result['manifest_sha256']!=digest(path):raise ValueError('Manifest mismatch')
        frame=pd.read_parquet(out/'reference_equity.parquet')
        if result['output_sha256']!=digest(out/'reference_equity.parquet'):raise ValueError('Equity receipt mismatch')
        if manifest['baseline_id']=='B0_CASH':
            expected=Decimal('1000')
            assert (frame.reference_equity==1000).all() and (frame.gross_notional==0).all()
        else:
            source=pd.read_feather(ROOT/manifest['data_receipt_hashes']['prices']['path']).set_index('date')
            terminal=Decimal(str(source.loc[frame.date.iloc[-1],'open']))
            fee=Decimal(str(manifest['cost_scenario']['total_bps_per_side']))/10000
            qty=Decimal(str(result['quantity']));entry=Decimal(str(result['entry_price']))
            flows=load(out/'funding_events.json')
            reconstructed_funding=sum((-qty*Decimal(str(e['mark']))*Decimal(str(e['rate'])) for e in flows),Decimal(0))
            expected=Decimal(1000)-qty*entry*(1+fee)+qty*terminal*(1-fee)+reconstructed_funding
            if abs(reconstructed_funding-Decimal(str(result['funding_net_usdt'])))>Decimal('.00000001'):raise ValueError('Funding reconciliation failed')
        error=abs(expected-Decimal(str(frame.reference_equity.iloc[-1])))
        if error>Decimal('.00000001'):raise ValueError('Terminal cash/inventory identity failed')
        rows.append({'id':result['id'],'state':result['state'],'baseline':manifest['baseline_id'],'market':manifest['market'],'window':manifest['window_id'],'cost_bps_per_side':manifest['cost_scenario']['total_bps_per_side'],'net_reference_return':result['metrics']['net_reference_return'],'reference_mdd':result['metrics']['reference_mdd'],'hourly_actual_mark_mdd':result.get('hourly_mark_mdd'),'verified_5m_futures_mark':False,'independent_terminal_cash_error_usdt':str(error),'output_sha256':result['output_sha256'],'result_sha256':digest(out/'result.json'),'manifest_sha256':digest(path),'natural_complete_campaigns':result['natural_complete_campaigns'],'terminal_open_positions':result['terminal_open_positions'],'economic_acceptance':'not_qualified_by_accounting_reference'})
    report={'schema_version':1,'count':len(rows),'all_terminal_identity_checks_passed':True,'max_terminal_cash_error_usdt':str(max(Decimal(r['independent_terminal_cash_error_usdt']) for r in rows)),'evidence_scope':'fixed accounting controls only, not16 independent alpha trials or evidence of executable fills','row_count_expected':16,'rows':rows,'orders_enabled':False,'production_eligible':False}
    if len(rows)!=16:raise ValueError('Missing controls')
    destination=HERE/'control_results.json'
    with destination.open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'}))

if __name__=='__main__':main()
