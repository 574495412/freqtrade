"""Rerun frozen official lookahead with in-memory read-only slice observation.

Never alters strategy or engine source; diagnostic backtests are synthetic and
have no economic interpretation. Raw per-slice trade records and missing-value
counts are saved independently so a reported signal difference can be examined.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

CZ=Path('/Users/chrise/GitHub/CZ-Agent')
FT=Path('/Users/chrise/GitHub/freqtrade')
BASE=FT/'user_data/crypto_quant/nfi_x8_lookahead_check_20261105_v001'
OUT=FT/'user_data/crypto_quant/nfi_x8_lookahead_inspect_20261105_v001'
PLAN=FT/'research/crypto/strategy_comparison_wide_20261105/lookahead-diagnostic-plan.json'

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def child():
    sys.dont_write_bytecode=True
    for k in list(os.environ):
        if k.startswith('FREQTRADE__'):del os.environ[k]
    sys.path.insert(0,str(CZ/'vendor/freqtrade'))
    import pandas as pd
    from freqtrade.optimize.analysis.lookahead import LookaheadAnalysis
    from freqtrade.main import main
    original=LookaheadAnalysis.analyze_row
    rows=[]
    def record(self,idx,result_row):
        original(self,idx,result_row)
        pair=result_row['pair']
        capture={'index':idx,'baseline':json.loads(result_row.to_json(date_format='iso')),'slices':{}}
        for label, holder in [('entry',self.entry_varHolders[idx]),('exit',self.exit_varHolders[idx])]:
            frame=holder.result['results']
            trades=json.loads(frame.to_json(orient='records',date_format='iso'))
            cut=holder.indicators[pair]
            full=self.full_varHolder.indicators[pair].loc[cut.index]
            # Retain target-end missingness separately: matching NaNs are not
            # considered different by DataFrame.compare.
            tail_full=full.tail(2);tail_cut=cut.tail(2)
            na_a=tail_full.isna();na_b=tail_cut.isna()
            capture['slices'][label]={'trades':trades,'rows':len(cut),
                'last_two_rows_both_missing_cells':int((na_a & na_b).to_numpy().sum()),
                'last_two_rows_missing_only_baseline':int((na_a & ~na_b).to_numpy().sum()),
                'last_two_rows_missing_only_slice':int((~na_a & na_b).to_numpy().sum()),
                'last_two_rows_dates_match':bool(tail_full['date'].equals(tail_cut['date']))}
        rows.append(capture)
        (OUT/'captured-slices.json').write_text(json.dumps(rows,indent=2)+'\n')
    LookaheadAnalysis.analyze_row=record
    args=json.loads((BASE/'process.json').read_text())['command'][1:]
    args[args.index('--userdir')+1]=str(OUT)
    args[args.index('--lookahead-analysis-exportfilename')+1]=str(OUT/'lookahead-result.csv')
    main(args)

def parent():
    base=json.loads((BASE/'process.json').read_text())
    if base['state']!='COMPLETED' or sha(BASE/'lookahead-result.csv')!=base['result_csv_sha256'] or sha(PLAN)!=base['plan_sha256']:
        raise ValueError('Original diagnostic or plan differs from completed receipt')
    OUT.mkdir(parents=True,exist_ok=False)
    env={k:v for k,v in os.environ.items() if not k.startswith('FREQTRADE__')}
    env['PYTHONDONTWRITEBYTECODE']='1'
    with (OUT/'inspect.log').open('x') as log:
        p=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--child'],cwd=CZ,env=env,stdout=log,stderr=subprocess.STDOUT)
        start=time.monotonic();peak=0;reason=None
        (OUT/'process.json').write_text(json.dumps({'pid':p.pid,'state':'RUNNING','orders_enabled':False},indent=2)+'\n')
        while p.poll() is None:
            try:rss=psutil.Process(p.pid).memory_info().rss
            except psutil.NoSuchProcess:break
            peak=max(peak,rss)
            if rss>int(5.5*1024**3) or time.monotonic()-start>900:
                reason='rss_guard' if rss>int(5.5*1024**3) else 'wall_guard';p.terminate();break
            time.sleep(2)
        try:code=p.wait(timeout=15)
        except subprocess.TimeoutExpired:p.kill();code=p.wait()
    source=CZ/'vendor/nfi-x8/NostalgiaForInfinityX8.py'
    plan=json.loads(PLAN.read_text())
    if sha(source)!=plan['source_sha256']:raise ValueError('Strategy source changed')
    result={'pid':p.pid,'state':'RESOURCE_ABORTED' if reason else 'COMPLETED' if code==0 else 'FAILED',
            'exit_code':code,'reason':reason,'elapsed_seconds':time.monotonic()-start,'peak_rss_bytes':peak,
            'script_sha256':sha(Path(__file__)),'plan_sha256':sha(PLAN),'base_diagnostic_sha256':sha(BASE/'process.json'),
            'log_sha256':sha(OUT/'inspect.log'),'source_sha256':sha(source),'orders_enabled':False}
    for filename in ('captured-slices.json','lookahead-result.csv'):
        path=OUT/filename
        result[filename+'_sha256']=sha(path) if path.exists() else None
    (OUT/'process.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)

if __name__=='__main__':
    child() if sys.argv[1:]==['--child'] else parent()
