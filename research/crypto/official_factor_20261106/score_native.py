"""Read official exported statistics; do not replace return or execution logic."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
RUNTIME=ROOT/'user_data/crypto_quant/official_factor_20261106'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def collect():
    plan=json.loads((HERE/'plan.json').read_text())
    rows=[]
    for run in plan['runs']:
        output=RUNTIME/'accounts'/run['id']
        attempts=[p for p in (RUNTIME/'accounts').glob(run['id']+'__retry*') if (p/'receipt.json').exists()]
        if attempts:output=sorted(attempts)[-1]
        receipt=output/'receipt.json'
        if not receipt.exists():continue
        r=json.loads(receipt.read_text())
        row={k:run[k] for k in ('id','strategy','market','window','cost')}
        row.update(returncode=r['returncode'],receipt=str(receipt.relative_to(ROOT)),receipt_sha256=sha(receipt))
        if r['returncode']!=0:
            row['status']='technical_failure';rows.append(row);continue
        archives=list(output.glob('*.zip'))
        if len(archives)!=1:
            row['status']='no_native_result_archive'
            row['note']='A zero CLI exit code alone is not backtest completion; inspect retained native.log'
            rows.append(row);continue
        archive=archives[0]
        if sha(archive)!=r['artifacts'][archive.name]:raise ValueError('result archive changed')
        with zipfile.ZipFile(archive) as z:
            names=[n for n in z.namelist() if n.endswith('.json') and not n.endswith('_config.json')]
            if len(names)!=1:raise ValueError('ambiguous official statistics')
            payload=json.loads(z.read(names[0]))
            stats=payload['strategy'][run['strategy']]
            cfg=json.loads(z.read(next(n for n in z.namelist() if n.endswith('_config.json'))))
            for key in ('total_trades','profit_total','profit_total_abs','final_balance','starting_balance',
                        'max_drawdown_account','max_drawdown_abs','max_drawdown_high','max_drawdown_low',
                        'max_relative_drawdown','backtest_start','backtest_end','market_change',
                        'profit_factor','winrate','sharpe','sortino','calmar','trade_count_long','trade_count_short'):
                if key in stats:row[key]=stats[key]
            row['native_pair_result_count']=sum(x.get('key')!='TOTAL' for x in stats.get('results_per_pair',[]))
            row['requested_pair_count']=len(plan['markets'][run['market']]['pairs'])
            row['resolved_timeframe']=cfg.get('timeframe')
            row['resolved_fee']=cfg.get('fee')
            row['native_exit_reasons']=stats.get('exit_reason_summary',[])
            row['native_pair_results']=stats.get('results_per_pair',[])
            row['forced_exit_trades']=sum(t.get('exit_reason')=='force_exit' for t in stats.get('trades',[]))
            # This is a sum of native exports, not an independent accounting validation.
            row['exported_funding_fees_sum']=sum(t.get('funding_fees',0) or 0 for t in stats.get('trades',[]))
            row['status']='native_backtest_completed'
            row['archive']=str(archive.relative_to(ROOT));row['archive_sha256']=sha(archive)
            row['economic_qualified']=False
        rows.append(row)
    return {'observed_at_utc':datetime.now(timezone.utc).isoformat(),'registered_accounts':24,'completed_or_failed':len(rows),
            'scope':'Official native cost-scenario exports; no executable-depth, independent OOS or live profitability claim',
            'native_endpoints_may_differ_by_strategy_timeframe':True,'production_eligible':False,'rows':rows}


if __name__=='__main__':
    result=collect()
    destination=RUNTIME/('native_scorecard_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')
    with destination.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps({'path':str(destination),'accounts':result['completed_or_failed']}))
    for r in result['rows']:
        print(r['id'],r.get('profit_total'),r.get('max_drawdown_account'),r['status'])
