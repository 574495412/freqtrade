"""Futures-only copy of frozen factory, using the existing official config fix.

Only config selection differs; historical order matching remains native OHLCV.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'research/crypto/rd_program_20261105/p0/environment'))
import guard


def build(output,strategy,market,scenario,start,end,datadir,metadata):
    guard.install_runtime_guards()
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.resolvers import ExchangeResolver
    from freqtrade.optimize.backtesting import Backtesting
    import pandas as pd
    if market!='futures':raise ValueError('Futures-only factory')
    if strategy not in {'F1FuturesCandleCost','F2FuturesCandleCost','AverageFuturesCandleControl'}:raise ValueError('Unregistered futures strategy')
    fix=json.loads((ROOT/'research/crypto/official_factor_20261106/technical_fix1.json').read_text())
    config_record=fix['configs'][scenario]
    source=ROOT/config_record['path']
    if hashlib.sha256(source.read_bytes()).hexdigest()!=config_record['sha256']:raise ValueError('Pinned futures config fix changed')
    raw=json.loads(source.read_text())
    raw.update(strategy=strategy,strategy_path=str(HERE),position_adjustment_enable=True,enable_protections=True,
        datadir=str(datadir),user_data_dir=str(output),exportdirectory=str(output/'native_results'),
        timerange=f'{int(pd.Timestamp(start).timestamp())}-{int(pd.Timestamp(end).timestamp())-1}',
        timeframe='5m' if strategy=='F1FuturesCandleCost' else '4h',db_url='sqlite://',
        timeframe_detail='' if strategy=='F1FuturesCandleCost' else '5m',
        unfilledtimeout=dict(entry=60,exit=120,exit_timeout_count=0,unit='seconds'),export='trades')
    output.mkdir(parents=True,exist_ok=True);(output/'native_results').mkdir(exist_ok=True)
    config_path=output/'config.json';config_path.write_text(json.dumps(raw,indent=2)+'\n')
    config=setup_optimize_configuration({'config':[str(config_path)],'datadir':str(datadir),
        'exportdirectory':output/'native_results'},RunMode.BACKTEST)
    if Path(config['datadir']).resolve()!=Path(datadir).resolve():raise ValueError('Resolved candle directory changed')
    exchange=ExchangeResolver.load_exchange(config,validate=False,load_leverage_tiers=False)
    markets=json.loads(Path(metadata).read_text())
    exchange._markets=copy.deepcopy(markets['markets']);exchange._leverage_tiers=copy.deepcopy(markets['leverage_tiers'])
    exchange._api.set_markets(markets['markets'],markets['currencies'])
    exchange._api_async.set_markets(markets['markets'],markets['currencies'])
    exchange.validate_config(config)
    account=Backtesting(config,exchange=exchange)
    if account.config['dry_run_wallet']!=1000 or account.strategylist[0].stoploss!=-.05:
        raise ValueError('Resolved native cost account contract changed')
    return account,exchange
