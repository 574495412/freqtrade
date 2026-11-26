"""Actual native Configuration -> Backtesting.start -> result ZIP on fixtures."""
import argparse
import importlib.util
import json
import os
import socket
import shutil
import sys
from pathlib import Path

from prepare_fixtures import HERE, ROOT, OUT, sha, save


def main():
    if any(k.startswith('FREQTRADE__') for k in os.environ):
        raise ValueError('Fixture rejects environment configuration overrides')
    parser=argparse.ArgumentParser();parser.add_argument('--case',required=True);parser.add_argument('--attempt',required=True)
    parser.add_argument('--inputs',default='fixture_inputs.json',choices=['fixture_inputs.json','fixture_inputs_maintenance_fixed.json']);args=parser.parse_args()
    engine=ROOT.parent/'CZ-Agent/vendor/freqtrade';manifest=json.loads((engine.parent/'FREQTRADE_SOURCE.json').read_text())
    assert manifest['commit']=='c051e9065c5b677f03817dd1f83cb980af1ed684'
    for name,digest in manifest['files'].items():assert sha(engine/name)==digest,name
    inputs=json.loads((HERE/args.inputs).read_text());case=next(c for c in inputs['cases']if c['name']==args.case)
    assert sha(HERE/'fixture_protocol.json')==inputs['protocol_sha256']
    for record in [case['config'],case['markets'],inputs['forecasts']]:assert sha(record['path'])==record['sha256']
    for path,digest in case['files'].items():assert sha(path)==digest
    output=OUT/'runs'/args.case/args.attempt;output.mkdir(parents=True,exist_ok=False)
    sources=output/'source_snapshot';sources.mkdir()
    bound={}
    observation=json.loads((HERE/'observation_protocol.json').read_text())
    for record in [observation['observer'],observation['clock']]:assert sha(record['path'])==record['sha256']
    for path in [HERE/'CopulaPortfolio.py',HERE/'FixturePortfolio.py',Path(__file__),HERE/'fixture_protocol.json',HERE/'observation_protocol.json',HERE/args.inputs,HERE.parent/'copula_execution_native_20261115_v3/ExecutionInputs.py',ROOT/'freqtrade/data/copula_execution.py',Path(observation['observer']['path']),Path(observation['clock']['path'])]:
        target=sources/path.name;shutil.copyfile(path,target);bound[str(path)]={'sha256':sha(path),'snapshot':str(target)}
    save(output/'implementation_freeze.json',bound)
    sys.path.insert(0,str(engine));sys.path.insert(0,str(HERE));sys.path.insert(0,str(HERE.parent/'copula_execution_native_20261115_v3'))
    import pandas as pd
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize.backtesting import Backtesting
    from freqtrade.persistence import LocalTrade
    from freqtrade.resolvers import ExchangeResolver
    raw=json.loads(Path(case['config']['path']).read_text());protocol=json.loads((HERE/'fixture_protocol.json').read_text())
    start,end=pd.Timestamp(protocol['start']),pd.Timestamp(protocol['end_exclusive'])
    raw.update(datadir=case['datadir'],user_data_dir=str(output),strategy_path=str(HERE),db_url='sqlite://',
        timerange=f'{int(start.timestamp())}-{int(end.timestamp())-1}',exportdirectory=str(output/'native_results'))
    (output/'native_results').mkdir();save(output/'config.json',raw)
    config=setup_optimize_configuration({'config':[str(output/'config.json')],'datadir':case['datadir'],'exportdirectory':output/'native_results'},RunMode.BACKTEST)
    assert config['runmode']==RunMode.BACKTEST and config['dry_run']and not any(config['exchange'].get(k)for k in ['key','secret','password'])
    assert config['fee']==protocol['costs_per_side'] and config['dry_run_wallet']==case['capital']
    def no_network(*a,**kw):raise RuntimeError('Synthetic replay must remain offline')
    socket.socket.connect=no_network
    exchange=ExchangeResolver.load_exchange(config,validate=False,load_leverage_tiers=False)
    metadata=json.loads(Path(case['markets']['path']).read_text());exchange._markets=metadata['markets'];exchange._leverage_tiers=metadata['leverage_tiers']
    exchange._api.set_markets(metadata['markets'],metadata['currencies']);exchange._api_async.set_markets(metadata['markets'],metadata['currencies'])
    exchange.validate_config(config)
    account=None
    try:
        account=Backtesting(config,exchange=exchange)
        def module(name,record):
            spec=importlib.util.spec_from_file_location(name,record['path'])
            loaded=importlib.util.module_from_spec(spec);spec.loader.exec_module(loaded)
            return loaded
        observer=module('frozen_fixture_observer',observation['observer']).Observer(account,
            {'datadir':case['datadir'],'observer_pairs':protocol['pairs'],
             'observation_start':protocol['start'],'observation_end':protocol['end_exclusive']},output)
        clock=module('frozen_fixture_clock',observation['clock']).attach(account,observer,
            start,end-pd.Timedelta(hours=1),case['capital'])
        account.start()
        strategy=account.strategy
        stats=account.results['strategy'][case['strategy']]
        save(output/'native_stats.json',stats);save(output/'strategy_trace.json',strategy.research_trace)
        assert not LocalTrade.bt_trades_open
        frame=pd.DataFrame(observer.rows)
        grid=pd.date_range(start,end,freq='h',inclusive='left')
        assert pd.DatetimeIndex(frame.date).as_unit('ns').equals(grid[1:].as_unit('ns'))
        assert not any(t['open_timestamp']<=int(start.timestamp()*1000)for t in stats['trades'])
        first=pd.DataFrame([{'date':start,'equity':case['capital'],'hourly_mark_equity':case['capital'],
            'open_trades':0,'gross':0.,'open_pnl':0.,'closed_realized_pnl':0.,'peak':case['capital'],'drawdown':0.}])
        frame=pd.concat([first,frame],ignore_index=True)
        assert pd.DatetimeIndex(frame.date).as_unit('ns').equals(grid.as_unit('ns'))
        assert frame.hourly_mark_equity.notna().all()
        frame.to_parquet(output/'hourly_equity.parquet',index=False)
        save(output/'observation_receipt.json',{'native_clock':clock,'actual_observations':len(observer.rows),
            'known_initial_flat_rows':1,'hourly_points':len(frame),
            'maximum_identity_error_usdt':observer.maximum_identity_error,
            'native_terminal_liquidation_separate':True,'synthetic_only':True})
        artifacts={'case':args.case,'attempt':args.attempt,'native_strategy':case['strategy'],'native_start_completed':True,
            'native_trades':len(stats['trades']),'native_orders':sum(len(t['orders'])for t in stats['trades']),
            'fatal_pair_error':strategy._fatal_error,'checked_native_clocks':strategy.research_checked_clocks,
            'native_wallet_final_balance':account.wallets.get_total('USDT'),'native_closed_profit':LocalTrade.bt_total_profit,
            'native_terminal_return_fixture_only':stats['profit_total'],'synthetic_only':True,'source20_funded_accounts':0,
            'native_zips':[{'path':str(p),'sha256':sha(p)}for p in sorted((output/'native_results').glob('*.zip'))]}
        assert artifacts['native_zips']
        for path,record in bound.items():assert sha(path)==record['sha256']
        save(output/'native_fixture_result.json',artifacts)
        print('NATIVE_FIXTURE',args.case,artifacts['native_trades'],artifacts['native_orders'],'fatal',strategy._fatal_error,flush=True)
    finally:
        if account is not None:account.cleanup()
        exchange.close()


if __name__=='__main__':main()
