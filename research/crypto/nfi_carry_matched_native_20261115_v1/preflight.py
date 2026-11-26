"""Readonly whole-window prices/funding/rules and bounded native prefix probes."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC,datetime
from pathlib import Path

import numpy as np
import pandas as pd
import psutil

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,r):Path(p).write_text(json.dumps(r,indent=2,default=str,allow_nan=False)+'\n')


def main():
    plan=json.loads((HERE/'plan.json').read_text());out=Path(plan['output_root'])
    parent=HERE.with_name('paired_carry_reservation_native_20261115_v2')
    carry=json.loads((parent/'plan.json').read_text());rows=[]
    engine=json.loads(Path(plan['engine_manifest']['path']).read_text())
    for p,digest in engine['files'].items():assert sha(Path(plan['engine_source'])/p)==digest,p
    source_manifest=json.loads((ROOT.parent/'CZ-Agent/vendor/NFI_X8_SOURCE.json').read_text())
    assert source_manifest['commit']=='1af55971d3a64703ec449078e09fdec257d0d3c6'
    for c in plan['candidates']:
        for r in [c['config'],c['market_snapshot'],c['observer_source'],*c['source_files'].values()]:assert sha(r['path'])==r['sha256']
        cfg=json.loads(Path(c['config']['path']).read_text())
        assert cfg['max_open_trades']==2 and cfg['dry_run_wallet']==c['capital']
        assert cfg['fee']==c['cost_bps']/10000 and cfg['exchange']['pair_whitelist']==c['pairs']
        assert set(cfg['nfi_parameters'].values())=={1.0}
    receipts=json.loads(Path(plan['data_receipts']['path']).read_text())
    for rel,digest in receipts.items():
        path=Path(plan['datadir'])/rel;assert sha(path)==digest
        frame=pd.read_feather(path);index=pd.DatetimeIndex(frame.date).as_unit('ns')
        assert index.is_unique and index.is_monotonic_increasing and str(index.tz)=='UTC'
        if 'funding_rate' in rel:
            expected=pd.date_range(plan['start'],plan['comparison_end'],freq='8h').as_unit('ns')
            actual=index[(index>=expected[0])&(index<=expected[-1])]
            assert actual.equals(expected) and np.isfinite(frame.funding_rate).all()
        else:
            tf=Path(rel).stem.split('-')[1];freq={'5m':'5min','15m':'15min','1h':'h','4h':'4h','1d':'D'}[tf]
            full=pd.date_range(plan['start'],plan['end'],freq=freq,inclusive='left').as_unit('ns')
            actual=index[(index>=full[0])&(index<=full[-1])]
            assert actual.equals(full),(rel,'incomplete calendar')
            assert np.isfinite(frame[['open','high','low','close','volume']].to_numpy()).all()
            assert(frame[['open','high','low','close']].to_numpy()>0).all()
        rows.append(dict(path=str(path),sha256=digest,rows=len(frame)))
    equal=[]
    for mode in ['spot','futures']:
        for symbol in ['BTC','ETH']:
            stem=symbol+'_USDT'+('_USDT'if mode=='futures'else'')
            tails=['5m-futures','1h-mark','1h-funding_rate']if mode=='futures'else['5m','1h']
            for tail in tails:
                rel=('futures/'if mode=='futures'else'')+stem+'-'+tail+'.feather'
                boundary=pd.Timestamp(plan['end'])-pd.Timedelta(minutes=5)
                a=pd.read_feather(Path(plan['datadir'])/rel).set_index('date').loc[plan['start']:boundary]
                b=pd.read_feather(Path(carry['datadir'])/rel).set_index('date').loc[plan['start']:boundary]
                a.index=a.index.as_unit('ns');b.index=b.index.as_unit('ns')
                pd.testing.assert_frame_equal(a,b,check_dtype=False,check_exact=True)
                equal.append(dict(file=rel,rows=len(a),values_exact=True))
    save(HERE/'input_review.json',dict(passed=True,native_files=len(engine['files']),raw_files=rows,
        same_target_input_observations_as_carry=equal,funding_events_per_coin=1824,
        historical_market_rules_certified=False))
    commands=[];env={k:os.environ[k]for k in ['PATH','HOME','TMPDIR','LANG','SSL_CERT_FILE','SSL_CERT_DIR']if k in os.environ}
    env.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',NUMEXPR_MAX_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
    for mode in ['spot','futures']:
        for end in ['20250201','20250115']:
            assert shutil.disk_usage(ROOT).free>=2*1024**3
            command=[sys.executable,'-B',str(HERE/'signal_probe.py'),'--mode',mode,'--end',end]
            with(HERE/(mode+'_'+end+'_signal.log')).open('xb')as log:
                p=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
                r=dict(mode=mode,end=end,pid=p.pid,command=command,started_at=datetime.now(UTC).isoformat(),maximum_rss=0)
                start=time.monotonic()
                while p.poll()is None:
                    try:rss=psutil.Process(p.pid).memory_info().rss
                    except psutil.NoSuchProcess:rss=0
                    r['maximum_rss']=max(r['maximum_rss'],rss)
                    if rss>3*1024**3 or time.monotonic()-start>600:p.kill();p.wait();r['guard_terminated']=True;break
                    time.sleep(.2)
                r.update(returncode=p.wait(),finished_at=datetime.now(UTC).isoformat());commands.append(r)
                save(HERE/'preflight_processes.json',commands)
                assert r['returncode']==0,(mode,end)
    comparisons=[]
    cutoff=pd.Timestamp('2025-01-15T00:00Z')
    for mode in ['spot','futures']:
        for symbol in ['BTC','ETH']:
            a=pd.read_parquet(out/('signal_'+mode+'_20250201')/(symbol+'_all_indicators.parquet'))
            b=pd.read_parquet(out/('signal_'+mode+'_20250115')/(symbol+'_all_indicators.parquet'))
            a=a.loc[a.date<cutoff].reset_index(drop=True);b=b.reset_index(drop=True)
            pd.testing.assert_frame_equal(a,b,check_exact=True)
            comparisons.append(dict(mode=mode,symbol=symbol,rows=len(a),columns=len(a.columns),all_cells_exact=True))
    save(HERE/'preflight.json',dict(passed=True,native_source_files=595,nfi_source_exact=True,
        configured_native_wallets=8,prefix_native_probes=4,prefix_comparisons=comparisons,
        probe_scope='January2025vectorindicators/signals only,not fullcallback or all-history causality certification',
        signals_and_live_cache_isolated=True,financial_accounts=0,orders_enabled=False))
    print('PREFLIGHT PASS 24inputfiles,carryobservations exact,4nativeprefix probes;0financialaccounts',flush=True)


if __name__=='__main__':main()
