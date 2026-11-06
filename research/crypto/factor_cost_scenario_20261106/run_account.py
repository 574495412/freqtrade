"""One pre-registered cost-scenario account, using official Backtesting only."""
import argparse
from datetime import datetime,timezone
import fcntl
import hashlib
import importlib.util
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import threading
import time
import traceback

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,v):
    with Path(p).open('x') as f:json.dump(v,f,ensure_ascii=False,indent=2,default=str);f.write('\n')
def utc():return datetime.now(timezone.utc).isoformat()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--run-id',required=True);args=ap.parse_args()
    plan=json.loads((HERE/'plan.json').read_text());row=next(r for r in plan['runs'] if r['id']==args.run_id)
    root=ROOT/'user_data/crypto_quant/factor_cost_scenario_20261106'
    root.mkdir(parents=True,exist_ok=True)
    with (ROOT/'user_data/crypto_quant/rd_program_20261105_native_worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if any(k.startswith('FREQTRADE__') for k in os.environ):raise ValueError('Environment configuration overrides forbidden')
        for name,h in plan['input_hashes'].items():
            if sha(ROOT/name)!=h:raise ValueError('Registered input changed: '+name)
        environment=json.loads((ROOT/plan['environment']).read_text())
        if any(version(n)!=v for n,v in environment['dependencies'].items()):raise ValueError('Pinned dependencies changed')
        if shutil.disk_usage(ROOT).free<10*1024**3:raise ValueError('Less than 10 GiB free disk')
        changed=subprocess.check_output(['git','diff','--name-only',plan['engine_commit'],'--','freqtrade'],cwd=ROOT,text=True).splitlines()
        if any(not p.startswith('freqtrade/research/') for p in changed):raise ValueError('Official native core changed')
        receipt=json.loads((ROOT/row['data_receipt']).read_text())
        for name,h in receipt.items():
            if sha(ROOT/row['datadir']/name)!=h:raise ValueError('Registered candle changed: '+name)
        output=root/row['id'];output.mkdir(exist_ok=False)
        write(output/'registration.json',dict(row=row,plan_sha256=sha(HERE/'plan.json'),started_at_utc=utc(),
              data_files_verified=len(receipt),orders_enabled=False,production_eligible=False))
        write(output/'process.json',dict(pid=os.getpid(),command=sys.argv,started_at_utc=utc()))
        start=time.monotonic();done=threading.Event();peak=[0]
        import psutil
        def monitor():
            process=psutil.Process()
            while not done.wait(1):
                peak[0]=max(peak[0],process.memory_info().rss)
                if peak[0]>plan['budget']['max_rss_bytes'] or time.monotonic()-start>plan['budget']['wall_seconds_per_run']:
                    write(output/'resource_failure.json',dict(peak_rss_bytes=peak[0],elapsed_seconds=time.monotonic()-start));os._exit(73)
        threading.Thread(target=monitor,daemon=True).start()
        a=exchange=None;financial_started=False
        try:
            import pandas as pd
            from native_factory import build
            from candle_model import CandleModel,PROFILE
            from freqtrade.persistence import LocalTrade
            a,exchange=build(output,row['strategy'],row['market'],row['cost'],row['start'],row['end'],
                ROOT/row['datadir'],ROOT/row['market_metadata'])
            hours={};prices={};marks={} if row['market']=='futures' else None
            directory=ROOT/row['datadir'];directory=directory/'futures' if row['market']=='futures' else directory
            for pair in a.config['exchange']['pair_whitelist']:
                stem=pair.replace('/','_').replace(':','_');suffix='-futures' if row['market']=='futures' else ''
                hours[pair]=pd.read_feather(directory/f'{stem}-1h{suffix}.feather')
                prices[pair]=pd.read_feather(directory/f'{stem}-5m{suffix}.feather').set_index('date').open
                if marks is not None:marks[pair]=pd.read_feather(ROOT/row['mark_paths'][pair]).set_index('date').open
            model=CandleModel(a,row['market'],row['cost'],row['start'],row['end'],hours,prices,marks=marks)
            a.strategylist[0].cost_model=model;model.attach()
            write(output/'financial_start.json',dict(at_utc=utc(),risk_profile=PROFILE,RD1_applied=False,
                simulated_shared_risk_rules=True,execution_capacity_verified=False))
            financial_started=True;a.start()
            if model.error:raise ValueError('Suppressed strategy callback error: '+model.error)
            frame=pd.DataFrame(model.rows)
            expected=pd.date_range(row['start'],row['end'],freq='5min',inclusive='left')
            if len(frame)!=len(expected) or not pd.DatetimeIndex(frame.date).equals(expected):raise ValueError('Incomplete 5m model equity calendar')
            frame.to_parquet(output/'model_equity.parquet',index=False)
            stats=a.results['strategy'][row['strategy']]
            if LocalTrade.bt_trades_open:raise ValueError('Native terminal accounting has unresolved trades')
            if any(t['is_short'] or t['leverage']!=1 for t in stats['trades']):raise ValueError('Unexpected short or leverage')
            audit_path=ROOT/'research/crypto/spot_baseline_20261105_v003/run_spot_comparison.py'
            spec=importlib.util.spec_from_file_location('frozen_cash_audit',audit_path)
            audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
            write(output/'cash_audit.json',audit.audit_trades(stats))
            fees=sum(sum(o['amount']*o['safe_price']*(t['fee_open'] if o['ft_is_entry'] else t['fee_close'])
                         for o in t['orders']) for t in stats['trades'])
            daily=frame.set_index('date').equity.resample('1D').last()
            result=dict(run_id=row['id'],finished_at_utc=utc(),financial_runs=1,risk_profile=PROFILE,
                evidence_class='observed_retrospective_candle_cost_scenario_not_independent_oos',
                net_model_return=float(frame.equity.iloc[-1]/1000-1),ending_model_equity=float(frame.equity.iloc[-1]),
                max_5m_model_drawdown=float(frame.drawdown.max()),natural_campaigns=sum(t['exit_reason']!='force_exit' for t in stats['trades']),
                terminal_open_campaigns=sum(t['exit_reason']=='force_exit' for t in stats['trades']),
                native_final_balance=stats['final_balance'],native_terminal_profit=stats['profit_total_abs'],
                fees_including_native_terminal=fees,funding_including_native_terminal=sum(t['funding_fees'] or 0 for t in stats['trades']),
                daily_nav={str(k):float(v) for k,v in daily.items()},rows=len(frame),hard_halt=bool(frame.hard_halt.any()),
                suppressed_callback_error=None,elapsed_seconds=time.monotonic()-start,peak_rss_bytes=peak[0],
                RD1_applied=False,simulated_shared_risk_rules=True,economic_qualified=False,production_eligible=False,
                orders_enabled=False,capacity_verified=False)
            write(output/'result.json',result)
            write(output/'artifact_hashes.json',{str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()})
            with (root/'execution_ledger.jsonl').open('a') as f:
                f.write(json.dumps(dict(run_id=row['id'],state='COMPLETE',financial_runs=1,result_sha256=sha(output/'result.json')))+'\n');f.flush();os.fsync(f.fileno())
            print(json.dumps({k:result[k] for k in ('run_id','net_model_return','max_5m_model_drawdown','natural_campaigns','terminal_open_campaigns')}),flush=True)
        except Exception as exc:
            write(output/'failure.json',dict(error_type=type(exc).__name__,reason=str(exc),traceback=traceback.format_exc(),financial_started=financial_started,at_utc=utc()))
            with (root/'execution_ledger.jsonl').open('a') as f:
                f.write(json.dumps(dict(run_id=row['id'],state='FAILED',financial_runs=int(financial_started),failure_sha256=sha(output/'failure.json')))+'\n');f.flush();os.fsync(f.fileno())
            raise
        finally:
            done.set()
            if a is not None:a.cleanup()
            if exchange is not None:exchange.close()


if __name__=='__main__':main()
