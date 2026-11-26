"""Freeze native inputs/configuration, execute actual native strategy preflight."""

import ast
import copy
import hashlib
import json
import os
import sys
from datetime import UTC,datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
OUT=ROOT/'user_data/crypto_quant/dual_thrust_native_20261114_v1'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def ref(path):return dict(path=str(Path(path).resolve()),sha256=sha(path))
def save(path,value):Path(path).write_text(json.dumps(value,indent=2,default=str)+'\n')


def original_thresholds(daily):
    tree=ast.parse((HERE/'sources/pre_0.txt').read_text())
    klass=next(x for x in tree.body if isinstance(x,ast.ClassDef))
    scope={'AlphaModel':object,'TradeBar':object}
    exec(compile(ast.Module(body=[klass],type_ignores=[]),'captured_source','exec'),scope)
    author=object.__new__(scope['PriceActionAlpha'])
    class Window:
        def __init__(self):self.rows=[]
        def add(self,bar):self.rows.append(bar);self.rows=self.rows[-4:]
        @property
        def is_ready(self):return len(self.rows)==4
    class Converter:
        def __getitem__(self,item):return lambda w:pd.DataFrame([vars(b) for b in w.rows])
    author.window=Window();author.algorithm=SimpleNamespace(pandas_converter=SimpleNamespace(get_data_frame=Converter()))
    rows=[]
    for day,row in daily.iterrows():
        author.on_consolidated(None,SimpleNamespace(**row[['open','high','low','close']].to_dict()))
        if author.window.is_ready:rows.append(dict(day=day,upper=author.buytrig,lower=author.selltrig))
    return pd.DataFrame(rows).set_index('day')


def main():
    assert not (HERE/'plan.json').exists(),'Preserve registered plan'
    protocol=json.loads((HERE/'protocol.json').read_text())
    assert sha(HERE/'protocol.json')==json.loads((HERE/'protocol_freeze.json').read_text())['protocol_sha256']
    OUT.mkdir(exist_ok=False);directory=OUT/'candles';(directory/'futures').mkdir(parents=True)
    previous=ROOT/'user_data/crypto_quant/onchain_hash_native_20261114_v1/v002'
    data={}
    for name,digest in json.loads((previous/'data_receipts.json').read_text()).items():
        if not name.startswith('futures/'):continue
        assert sha(previous/'candles'/name)==digest
        os.link(previous/'candles'/name,directory/name);data[name]=digest
    assert len(data)==5
    save(OUT/'data_receipts.json',data)
    plan=json.loads((HERE.parent/'tsmom_risk_native_20261114_v1/v002/plan.json').read_text())
    for k in ['technical_change','original_frozen_plan','alpha_parameters_changed','financial_output_already_observed']:
        plan.pop(k,None)
    plan.update(schema='freqtrade.native.dual-thrust-local.v1',registered_at=datetime.now(UTC).isoformat(),
                output_root=str(OUT),datadir=str(directory),data_receipts=ref(OUT/'data_receipts.json'),
                pairs=['BTC/USDT:USDT'],allowed_leverage=1.0,candidates=[],
                total_cost_bps_per_side=[15,8],additional_source_seals=[ref(HERE/'protocol.json'),ref(HERE/'sources/qc_crypto_embedded.html'),ref(HERE/'sources/pre_0.txt'),ref(HERE/'sources/pre_1.txt')])
    plan['marks']={'BTC/USDT:USDT':ref(directory/'futures/BTC_USDT_USDT-1h-mark.feather')}
    for name in protocol['main_classes']:
        for capital in [1000,500]:
            config=json.loads((HERE.parent/'pairs_cointegration_native_20261114_v1/TrendGrossOne_1000.json').read_text())
            config.pop('research_decision_end',None)
            config.update(strategy=name,dry_run_wallet=capital,position_adjustment_enable=False,
                          max_entry_position_adjustment=0,max_open_trades=1,margin_mode='isolated',
                          timeframe='1h',startup_candle_count=600,timeframe_detail='5m')
            config['exchange']['pair_whitelist']=plan['pairs']
            path=HERE/f'{name}_{capital}.json';save(path,config)
            plan['candidates'].append(dict(name=f'{name}_{capital}',strategy=name,capital=capital,
                 pairs=plan['pairs'],config=ref(path),source_files={n:ref(HERE/n) for n in ['DualPortfolio.py','DualRules.py']}))
    save(HERE/'plan.json',plan)
    engine=Path(plan['engine_source']);manifest=json.loads(Path(plan['engine_manifest']['path']).read_text())
    for name,digest in manifest['files'].items():assert sha(engine/name)==digest,name
    sys.path.insert(0,str(engine));sys.path.insert(0,str(HERE))
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.enums import RunMode
    from freqtrade.resolvers import ExchangeResolver,StrategyResolver
    snapshot=json.loads(Path(plan['market_snapshot']['path']).read_text());checks=[]
    for candidate in plan['candidates'][::2]:
        config=json.loads(Path(candidate['config']['path']).read_text())
        config.update(runmode=RunMode.BACKTEST,strategy_path=str(HERE),datadir=directory,
                      user_data_dir=ROOT/'user_data',timerange=f"{int(pd.Timestamp(plan['start']).timestamp())}-{int(pd.Timestamp(plan['end']).timestamp())-1}")
        strategy=StrategyResolver.load_strategy(config)
        exchange=ExchangeResolver.load_exchange(config,validate=False,load_leverage_tiers=False)
        try:
            exchange._markets=snapshot['markets'];exchange._leverage_tiers=snapshot['leverage_tiers']
            exchange._api.set_markets(snapshot['markets'],snapshot['currencies'])
            exchange._api_async.set_markets(snapshot['markets'],snapshot['currencies'])
            exchange.validate_config(config)
            strategy.dp=DataProvider(config,exchange);strategy.bot_start()
            fields=strategy.research_features[plan['pairs'][0]]
            raw=strategy.dp.get_pair_dataframe(plan['pairs'][0],'1h','futures')
            assert len(strategy.research_weights)==14592
            if strategy.mode=='dual':
                from DualRules import completed_states
                _,daily=completed_states(raw,'dual');expected=original_thresholds(daily)
                np.testing.assert_array_equal(expected[['upper','lower']].to_numpy(),daily.loc[expected.index,['upper','lower']].to_numpy())
                save(HERE/'source_threshold_parity.json',dict(passed=True,actual_source_methods=len(expected),threshold_cells=2*len(expected),source=ref(HERE/'sources/pre_0.txt'),anchor='Latest completed daily open'))
            refits=0
            for cutoff in pd.to_datetime(['2025-05-04','2026-01-01','2026-07-01'],utc=True):
                before=fields.clock.le(cutoff)
                changed=raw.copy(); changed.loc[changed.date.ge(cutoff),['open','high','low','close']]*=11
                for altered in [changed,raw.loc[raw.date.lt(cutoff)]]:
                    actual=StrategyResolver.load_strategy(copy.deepcopy(config))
                    actual.dp=SimpleNamespace(get_pair_dataframe=lambda *a,altered=altered,**kw:altered.copy())
                    actual.bot_start();got=actual.research_features[plan['pairs'][0]]
                    pd.testing.assert_frame_equal(fields.loc[before].reset_index(drop=True),got.loc[got.clock.le(cutoff)].reset_index(drop=True),check_exact=True)
                    refits+=1
            strategy.research_weights.to_parquet(OUT/f'preflight_weights_{candidate["strategy"]}.parquet')
            fields.to_parquet(OUT/f'preflight_features_{candidate["strategy"]}.parquet',index=False)
            advised=strategy.advise_exit(strategy.advise_entry(strategy.advise_indicators(raw.copy(),{'pair':plan['pairs'][0]}),{'pair':plan['pairs'][0]}),{'pair':plan['pairs'][0]})
            forbidden=(advised.date+pd.Timedelta(hours=1)).dt.hour.isin([0,8,16])
            assert not advised.loc[forbidden,['enter_long','exit_long','enter_short','exit_short']].ne(0).any().any()
            checks.append(dict(strategy=candidate['strategy'],actual_native_prefix_refits=refits,hourly_weights=len(strategy.research_weights),signal_rows=len(advised),forbidden_funding_hour_signals=0))
        finally:exchange.close()
    save(HERE/'preflight.json',dict(passed=True,checks=checks,native_source_files_verified=len(manifest['files']),plan=ref(HERE/'plan.json'),financial_runs_started=False))
    print(json.dumps(checks,indent=2))


if __name__=='__main__':main()
