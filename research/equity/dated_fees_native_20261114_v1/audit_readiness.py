"""Input inventory only; do not construct alpha scores or account returns."""
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE=Path(__file__).resolve().parent
VIBE=Path('/Users/chrise/GitHub/Vibe-Trading')
def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main():
    original=read(HERE/'protocol.json');assert sha(original['parent_plan'])==original['parent_plan_sha256']
    plan=read(original['parent_plan']);stock_rows=plan['records']
    assert len(stock_rows)==514
    parent=VIBE/'agent/research/capex_growth_source_native_20261114_v1/protocol.json';p=read(parent)
    members=read(p['inputs']['issuer_plan'])['issuer_members'];mapping={s+'.US':cik for cik,names in members.items() for s in names}
    assert len(mapping)==sum(len(v)for v in members.values())
    inventory=[];bindings={str(parent):sha(parent)};status=Counter();periods=Counter()
    now=datetime.fromisoformat('2025-01-01T00:00:00+00:00')
    for row in stock_rows:
        symbol=row['symbol'];record={'symbol':symbol,'source':row['source'],'source_sha256_verified':sha(row['source'])==row['source_sha256']}
        assert record['source_sha256_verified']
        if symbol not in mapping:
            record['status']='NO_EXISTING_EXACT_ISSUER_BINDING'
        else:
            cik=mapping[symbol];path=Path(p['inputs']['financial_source_root'])/(cik+'.json')
            assert sha(path)==p['input_hashes'][str(path)]
            source=read(path);bindings[str(path)]=sha(path)
            annuals=[a for a in source['annual_records'].values() if a.get('available_at') and datetime.fromisoformat(a['available_at'])<=now]
            ends=sorted({a['period_end']for a in annuals});periods[len(ends)]+=1
            state=source['states'].get('2024-12-31',{});annual=source['annual_records'].get(state.get('accession'),{})
            fields=annual.get('fields',{});capex=fields.get('capex',{});revenue=fields.get('revenue',{})
            paired=bool(state.get('status')=='DISCLOSURE_CHECKED_NOT_SECURITY_ADMITTED' and capex.get('status')=='AVAILABLE' and revenue.get('status')=='AVAILABLE' and capex.get('start')==revenue.get('start')==annual.get('annual_start') and capex.get('end')==revenue.get('end')==annual.get('period_end'))
            record.update(cik=cik,annual_source_path=str(path),annual_source_sha256=sha(path),distinct_fiscal_ends_before_start=ends,latest_state=state,capex_field_status=capex.get('status','MISSING'),revenue_field_status=revenue.get('status','MISSING'),same_period_fields_in_existing_backbone=paired,status='ANNUAL_BACKBONE_ONLY_NOT_SOURCE_MONTHLY_PANEL')
        status[record['status']]+=1;inventory.append(record)
    result={'passed_inventory_checks':True,'scope':'original514 inputs retained; source readiness only','stage':'before factor values','source_definition':'capital expenditure/revenue divided by trailing36 calendar-month mean; minimum24 monthly observations; revenue>=10million source units; source score direction not frozen here','source_monthly_panel_admitted':False,'no_annual_count_substitution':'Independent fiscal periods are not the source monthly rolling observation count; do not reject or accept based on annual count alone','no_proxy':'No monthly carry/zero/PPE proxy or source availability convention silently supplied','status_counts':dict(status),'distinct_fiscal_ends_distribution_before_window':dict(periods),'latest_same_period_reported_field_pairs':sum(r.get('same_period_fields_in_existing_backbone',False)for r in inventory),'bindings':bindings,'records':inventory,'new_data_requests':0,'new_factor_scores':0,'new_registry_definitions':0,'factor_analysis_calls':0,'financial_accounts':0,'economic_hypothesis_tested':False}
    (HERE/'investment_input_readiness.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items()if k not in ['bindings','records']},indent=2))
if __name__=='__main__':main()
