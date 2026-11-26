"""Actual native January indicator prefix probe; no financial lifecycle."""
import argparse
import hashlib
import importlib.util
import json
import socket
import sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


def save(p,r):Path(p).write_text(json.dumps(r,indent=2,default=str,allow_nan=False)+'\n')


def main(mode,end):
    plan=json.loads((HERE/'plan.json').read_text())
    sys.path.insert(0,plan['engine_source'])
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize.backtesting import Backtesting
    from freqtrade.persistence import Trade
    from freqtrade.resolvers import ExchangeResolver
    candidate=next(c for c in plan['candidates']if c['mode']==mode)
    out=Path(plan['output_root'])/('signal_'+mode+'_'+end)
    out.mkdir(exist_ok=False)
    cfg=json.loads(Path(candidate['config']['path']).read_text())
    cfg.update(datadir=plan['datadir'],user_data_dir=str(out),strategy_path=str(HERE/'sources'),
        timerange='20250101-'+end,db_url='sqlite://',export='none')
    save(out/'config.json',cfg)
    config=setup_optimize_configuration({'config':[str(out/'config.json')],'datadir':plan['datadir']},RunMode.BACKTEST)
    exchange=ExchangeResolver.load_exchange(config,validate=False,load_leverage_tiers=False)
    market=json.loads(Path(candidate['market_snapshot']['path']).read_text())
    exchange._markets=market['markets'];exchange._leverage_tiers=market['leverage_tiers']
    exchange._api.set_markets(market['markets'],market['currencies'])
    exchange._api_async.set_markets(market['markets'],market['currencies'])
    exchange.validate_config(config)
    def offline(*args,**kwargs):raise RuntimeError('Native signal probe offline')
    socket.socket.connect=offline
    account=None;prior=Trade.use_db
    try:
        source=Path(plan['price_precision_source']['path'])
        spec=importlib.util.spec_from_file_location('matched_precision',source)
        precision=importlib.util.module_from_spec(spec);spec.loader.exec_module(precision)
        class Probe(precision.MatchedPricePrecisionMixin,Backtesting):pass
        account=Probe(config,exchange=exchange);account.setup_frozen_price_precision(pairs=tuple(candidate['pairs']))
        strategy=account.strategylist[0];account._set_strategy(strategy)
        data,timerange=account.load_bt_data()
        processed=strategy.advise_all_indicators(data)
        rows=[]
        for pair,frame in processed.items():
            flags=strategy.ft_advise_signals(frame,{'pair':pair})
            flags.to_parquet(out/(pair.split('/')[0]+'_all_indicators.parquet'),index=False)
            fields=['date','open','high','low','close','volume','enter_long','enter_short','exit_long','exit_short','enter_tag']
            fields += ['exit_tag']if'exit_tag'in flags else[]
            flags[fields].to_parquet(out/(pair.split('/')[0]+'_signals.parquet'),index=False)
            rows.append(dict(pair=pair,rows=len(flags),columns=len(flags.columns),
                first=str(flags.date.min()),last=str(flags.date.max())))
        save(out/'receipt.json',dict(passed=True,mode=mode,end=end,records=rows,
            strategy_file_sha256=hashlib.sha256((HERE/'sources/NostalgiaForInfinityX8.py').read_bytes()).hexdigest(),
            leverage_modes=[strategy.futures_mode_leverage,strategy.futures_mode_leverage_rebuy_mode,strategy.futures_mode_leverage_grind_mode],
            source_core_unchanged=True,source_original_timeframe=strategy.timeframe,slots=2,
            full_financial_backtest_started=False,nativeTrades=0,
            isolated_runtime_cache_files=[p.name for p in out.glob('nfix8*')]))
        print(mode,end,'native input/flags',rows,flush=True)
    finally:
        if account is not None:account.cleanup()
        exchange.close();Trade.use_db=prior


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['spot','futures'],required=True)
    p.add_argument('--end',choices=['20250115','20250201'],required=True)
    a=p.parse_args();main(a.mode,a.end)
