"""Scoped observed input inventory and pure source staleness diagnostic."""
import ast
import hashlib
import json
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import requests

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
VIBE=Path('/Users/chrise/GitHub/Vibe-Trading')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def save(name,obj):(HERE/name).write_text(json.dumps(obj,indent=2,default=str)+'\n')

class Visible(HTMLParser):
    def __init__(self):super().__init__();self.skip=0;self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.skip+=1
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.skip=max(0,self.skip-1)
    def handle_data(self,data):
        if not self.skip and data.strip():self.parts.append(data.strip())

def social():
    for name in ['x_pairs.html','youtube.html','douyin.html']:
        parser=Visible();parser.feed((HERE/'sources'/name).read_text())
        (HERE/'sources'/(name+'.visible.txt')).write_text('\n'.join(parser.parts)+'\n')
    text=(HERE/'sources/youtube.html').read_text()
    response=None
    for match in re.finditer(r'ytInitialPlayerResponse\s*=\s*',text):
        try:response=json.JSONDecoder().raw_decode(text[match.end():])[0];break
        except json.JSONDecodeError:continue
    if response:
        video=response.get('videoDetails',{});captions=response.get('captions',{})
        save('youtube_metadata.json',{'video':{k:video.get(k)for k in ['videoId','title','author','shortDescription']},'captions':captions,'video_watched':False})
        tracks=captions.get('playerCaptionsTracklistRenderer',{}).get('captionTracks',[])
        if tracks:
            url=tracks[0]['baseUrl']+'&fmt=json3'
            try:
                r=requests.get(url,timeout=25);path=HERE/'sources/captions.json';path.write_bytes(r.content)
                save('caption_receipt.json',{'url':url,'status':r.status_code,'bytes':len(r.content),'path':str(path),'sha256':sha(path),'transcript_read':False})
            except requests.RequestException as exc:save('caption_receipt.json',{'error':str(exc),'transcript_read':False})

def source_gap():
    path=HERE/'sources/CustomerMomentum.py';tree=ast.parse(path.read_text())
    main=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='main')
    # Execute only the exact source's in-memory annual-link stop-row/merge/
    # forward-fill statements. No source I/O, returns, factor or portfolio code.
    first=next(i for i,n in enumerate(main.body)if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='temp1b' for t in n.targets))
    last=next(i for i,n in enumerate(main.body)if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='seg_customer3' for t in n.targets))
    selected=main.body[first:last]
    assert not any(isinstance(n,(ast.Import,ast.ImportFrom))for n in ast.walk(ast.Module(body=selected,type_ignores=[])))
    temp1=pd.DataFrame({'permno':[1,1,2],'datadate':pd.to_datetime(['2018-06-28','2021-06-28','2019-06-28']),'customer1':[10,20,30]})
    dates=pd.date_range('2018-12-01','2021-12-01',freq='MS')+pd.Timedelta(days=27)
    env={'pd':pd,'temp1':temp1,'customer_cols':['customer1'],'tempm0':pd.DataFrame({'permno':1,'time_avail_m':dates}),'logger':SimpleNamespace(info=lambda *a:None)}
    exec(compile(ast.Module(body=selected,type_ignores=[]),str(path),'exec'),env)
    actual=env['tempm1'];stale=actual[(actual.time_avail_m>pd.Timestamp('2019-12-28'))&(actual.time_avail_m<pd.Timestamp('2021-12-28'))]
    assert len(stale)==23 and stale.customer1.eq(10).all()
    result={'source_sha256':sha(path),'source_statement_lines':[selected[0].lineno,selected[-1].end_lineno], 'synthetic_input':temp1.to_dict('records'),'source_lastentry_rows':env['temp1b'].to_dict('records'),'observed_stale_months_after_first_12':len(stale),'conclusion':'In this fixed Python source, a same-supplier multi-year annual gap does not insert the advertised 12-month stop. Do not adopt the comment as verified staleness protection.','source_financial_engine_executed':False,'real_factor_values':0,'economic_hypothesis_tested':False}
    save('source_staleness_diagnostic.json',result)

def inventory():
    parent=HERE.parent/'dated_fees_native_20261114_v1/investment_input_readiness.json'
    old=read(parent);p=read(HERE.parent/'native_session_admission_20261114_v1/plan.json')
    assert len(p['records'])==len(old['records'])==514
    records=[];bindings={str(parent):sha(parent)};counts=Counter()
    seen={}
    for row in old['records']:
        source=Path(row['source']);h=sha(source)
        planrow=next(r for r in p['records']if r['symbol']==row['symbol'])
        assert h==planrow['source_sha256']
        columns=pd.read_csv(source,nrows=0).columns.tolist()
        # Existing factor CSVs do not supply historical customer identity links.
        required={'cust_permno','linkdt_cust','linkenddt_cust','customer_known_at'}
        assert not required.intersection(columns)
        item={'symbol':row['symbol'],'source':str(source),'sha256':h,'customer_link_columns_found':[], 'status':'NO_ADMITTED_HISTORICAL_CUSTOMER_LINK_PANEL'}
        if row.get('annual_source_path'):
            path=Path(row['annual_source_path']);assert sha(path)==row['annual_source_sha256']
            if path not in seen:
                raw=read(path)
                fields=sorted({k for a in raw['annual_records'].values()for k in a.get('fields',{})})
                assert not any('customer' in k.lower()for k in fields)
                seen[path]=fields;bindings[str(path)]=sha(path)
            item['cik']=row['cik'];item['annual_fields_without_customer_identity']=seen[path]
        counts[item['status']]+=1;records.append(item)
    save('customer_input_admission.json',{'scope':'original514 price/feature CSVs and bound annual backbones only; not an exhaustive search of all filings or the machine','original_window':['2025-01-01','2026-08-31'],'status_counts':dict(counts),'bound_annual_input_count':sum('cik'in r for r in records),'unique_annual_backbones':len(seen),'source_required_inputs':['CompustatSegmentDataCustomers.csv','CCMLinkingTable.parquet','monthlyCRSP.parquet'],'source_availability_convention':'annual datadate plus6months; not certified historical disclosure','source_returns':'equal mean of available linked customer ret_b4_dl; missing customer returns dropped, not zero; use owning save_standardized timing before any evaluation','records':records,'bindings':bindings,'new_factor_values':0,'registry_definitions':0,'factor_analysis_calls':0,'historical_strategy_accounts':0,'economic_hypothesis_tested':False})

if __name__=='__main__':
    social();source_gap();inventory();print('Social parsing, exact-source staleness diagnostic and514-input admission complete')
