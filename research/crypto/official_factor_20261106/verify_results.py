"""Verify exported official contracts and immutable input payloads, not profitability."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import zipfile

from run_one import sha
from score_native import collect, HERE, ROOT, RUNTIME


def main():
    plan=json.loads((HERE/'plan.json').read_text())
    score=collect()
    if len(score['rows'])!=24 or any(r['status']!='native_backtest_completed' for r in score['rows']):
        raise ValueError('not all registered native accounts produced results')
    checks=[]
    for row in score['rows']:
        with zipfile.ZipFile(ROOT/row['archive']) as z:
            s=json.loads(z.read(next(n for n in z.namelist() if n.endswith('.json') and not n.endswith('_config.json'))))['strategy'][row['strategy']]
            c=json.loads(z.read(next(n for n in z.namelist() if n.endswith('_config.json'))))
            spec=plan['strategies'][row['strategy']]
            expected_fee=next(x['total_bps_per_side']/10000 for x in plan['markets'][row['market']]['cost_scenarios'] if x['id']==row['cost'])
            passed={
                'initial_balance_1000':s['starting_balance']==1000,
                'fixed_pairlist_and_order':s['pairlist']==plan['markets'][row['market']]['pairs'],
                'original_timeframe':s['timeframe']==spec['timeframe'],
                'original_stoploss':s['stoploss']==spec['stoploss'],
                'original_roi':s['minimal_roi']==spec['roi'],
                'cost_scenario':c['fee']==expected_fee,
                'max_open_trades':s['max_open_trades_setting']==6,
                'original_stake_policy':s['stake_amount']=='unlimited',
                'one_x':all(t.get('leverage',1)==1 for t in s['trades']),
                'long_only':not any(t['is_short'] for t in s['trades']),
                'native_final_identity':abs(s['final_balance']-s['starting_balance']-s['profit_total_abs'])<.01,
                'exported_upstream_source':z.read(next(n for n in z.namelist() if n.endswith('_'+row['strategy']+'.py')))==(HERE/'sources'/(row['strategy']+'.py')).read_bytes(),
            }
            if not all(passed.values()):raise ValueError((row['id'],passed))
            checks.append({'run_id':row['id'],'checks':passed,'archive_sha256':row['archive_sha256']})
    data={}
    for run in plan['runs']:
        path=run['data_receipt']['path']
        if path in data:continue
        if sha(ROOT/path)!=run['data_receipt']['sha256']:raise ValueError('changed data manifest')
        hashes=json.loads((ROOT/path).read_text())
        for name,expected in hashes.items():
            if sha(ROOT/run['datadir']/name)!=expected:raise ValueError('changed data payload '+name)
        data[path]={'sha256':sha(ROOT/path),'payloads_verified':len(hashes)}
    for rel,expected in json.loads((HERE/'freeze.json').read_text())['files'].items():
        if sha(ROOT/rel)!=expected:raise ValueError('changed frozen research source '+rel)
    changed=subprocess.check_output(['git','diff','--name-only',plan['engine_commit'],'--','freqtrade'],cwd=ROOT,text=True).splitlines()
    if any(not p.startswith('freqtrade/research/') for p in changed):raise ValueError('official financial core changed')
    result={'verified_at_utc':datetime.now(timezone.utc).isoformat(),'accounts':24,'all_checks_passed':True,
            'official_engine_core_unchanged':True,'data_receipts':data,'per_account':checks,
            'retained_configuration_failures':score['retained_failed_attempts'],
            'new_causality_diagnostics_run':0,'profitability_qualified':False,'production_eligible':False,
            'orders_enabled':False,'scope':'Official source/config/result identity and input immutability only'}
    with (HERE/'verification.json').open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    with (RUNTIME/'complete_native_scorecard.json').open('x') as f:json.dump(score,f,indent=2);f.write('\n')
    print(json.dumps({'accounts':24,'checks_passed':True,'profitable_accounts':sum(r['profit_total']>0 for r in score['rows']),
                      'data_payloads_verified':sum(r['payloads_verified'] for r in data.values())}))


if __name__=='__main__':main()
