"""Freeze explicit synthetic contracts/data before native lifecycle runs."""
import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'user_data/crypto_quant/copula_fills_native_20261115_v4'
PARENT = ROOT / 'research/crypto/copula_execution_native_20261115_v3'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, obj):
    path.write_text(json.dumps(obj, indent=2, default=str) + '\n')


def main():
    assert not (HERE / 'fixture_protocol.json').exists()
    OUT.mkdir(exist_ok=True, parents=True)
    protocol = dict(scope='Native synthetic lifecycle fixtures, not source strategy financial evidence',
        start='2025-01-02T00:00Z', end_exclusive='2025-01-04T02:00Z',
        pairs=['FAKEONE/USDT:USDT', 'FAKETWO/USDT:USDT'],
        fixture_market_specs='Synthetic .1coin quantity tick/min, .001price tick,5USDT notional minimum,3marginleverage; not EOS or venue certification',
        costs_per_side=.0015, initial_tradable_ratio=.99,
        cases={'normal1000': {'capital':1000,'strategy':'FixtureCopula','expected':'complete'},
               'normal500': {'capital':500,'strategy':'FixtureCopula','expected':'complete'},
               'under_minimum': {'capital':1000,'strategy':'FixtureCopula','expected':'both_legs_rejected'},
               'one_leg_rejected': {'capital':1000,'strategy':'FixtureRejectSecond','expected':'native_pair_mismatch_detected'},
               'missing_funding_exposed': {'capital':1000,'strategy':'FixtureCopula','expected':'actual_missing_funding_exposure_detected'},
               'missing_funding_flat': {'capital':1000,'strategy':'FixtureCopula','expected':'unexposed_gap_retained'}},
        same_native_engine='c051e9065c5b677f03817dd1f83cb980af1ed684',
        parent_manifest={'path':str(PARENT/'artifact_manifest.json'),'sha256':sha(PARENT/'artifact_manifest.json')},
        source20_funded_admission=False, orders_enabled=False, services_enabled=False)
    save(HERE / 'fixture_protocol.json', protocol)
    parent = json.loads((ROOT / 'research/crypto/pairs_cointegration_native_20261114_v1/plan.json').read_text())
    raw_config = json.loads(Path(parent['candidates'][0]['config']['path']).read_text())
    snapshot = json.loads(Path(parent['market_snapshot']['path']).read_text())
    template = copy.deepcopy(snapshot['markets']['XRP/USDT:USDT'])
    start, end = pd.Timestamp(protocol['start']), pd.Timestamp(protocol['end_exclusive'])
    times = pd.date_range(start, end, freq='5min', inclusive='left')
    hours = pd.date_range(start, end, freq='h', inclusive='left')
    states=[]
    for t in hours:
        if t.day == 2: state=1 if t.hour < 6 or t.hour >=22 else (-1 if 8 <= t.hour < 12 else 0)
        else: state=-1 if t.day == 3 and t.hour < 4 else (1 if t.day == 4 or t.hour >=10 else 0)
        states.append(state)
    forecasts = pd.DataFrame(dict(week=[('2024-12-27T00:00Z'if t.day==2 else '2025-01-03T00:00Z')for t in hours],
        model_status='FITTED', state_10=states, state_15=states, state_20=states,
        symbol1='FAKEONE', symbol2='FAKETWO', beta1=10., beta2=20.),index=hours)
    fpath=OUT/'synthetic_forecasts.parquet';forecasts.to_parquet(fpath)
    artifacts=[]
    for name, case in protocol['cases'].items():
        folder=OUT/'fixtures'/name;data=folder/'candles/futures';data.mkdir(parents=True,exist_ok=False)
        markets,tiers={},{}
        for index,pair in enumerate(protocol['pairs']):
            base=pair.split('/')[0];native=base+'_USDT_USDT'
            price=(100. if index==0 else 50.)+np.arange(len(times))*(.05 if index==0 else .03)
            quote=pd.DataFrame({'date':times,'open':price,'high':price+.01,'low':price-.01,'close':price,'volume':100000.})
            quote.to_feather(data/f'{native}-5m-futures.feather')
            marks=quote.iloc[np.arange(len(hours))*12].reset_index(drop=True).copy();marks['volume']=0.
            marks.to_feather(data/f'{native}-1h-mark.feather')
            fund=pd.DataFrame({'date':hours,'funding_rate':.0001})
            if name=='missing_funding_exposed'and index==0:fund=fund.loc[fund.date.ne(pd.Timestamp('2025-01-03T11:00Z'))]
            if name=='missing_funding_flat'and index==0:fund=fund.loc[fund.date.ne(pd.Timestamp('2025-01-02T07:00Z'))]
            fund.reset_index(drop=True).to_feather(data/f'{native}-1h-funding_rate.feather')
            m=copy.deepcopy(template);m.update(id=base+'USDT',symbol=pair,base=base,baseId=base,quote='USDT',quoteId='USDT',settle='USDT',settleId='USDT',active=True,contractSize=1.)
            m['precision']['amount']=.1;m['precision']['price']=.001;m['limits']['amount']['min']=.1;m['limits']['amount']['max']=1000000.
            m['limits']['cost']['min']=100000. if name=='under_minimum'and index==1 else 5.
            m['limits']['cost']['max']=10000000.
            m['info'].update(symbol=base+'USDT',pair=base+'USDT',baseAsset=base,quoteAsset='USDT',marginAsset='USDT',status='TRADING',contractType='PERPETUAL')
            markets[pair]=m
            tiers[pair]=[{'tier':1.,'symbol':pair,'currency':'USDT','minNotional':0.,'maxNotional':1000000.,'maintenanceMarginRate':.005,'maxLeverage':75.,'info':{'bracket':1,'initialLeverage':75,'notionalCap':1000000,'notionalFloor':0,'maintMarginRatio':.005,'cum':0}}]
        market_path=folder/'synthetic_markets.json';save(market_path,dict(markets=markets,leverage_tiers=tiers,currencies=snapshot['currencies']))
        config=copy.deepcopy(raw_config)
        config.pop('timeframe_detail',None)
        config.update(strategy=case['strategy'],timeframe='5m',startup_candle_count=0,dry_run=True,dry_run_wallet=case['capital'],
            stake_amount='unlimited',max_open_trades=2,tradable_balance_ratio=.99,position_adjustment_enable=False,trailing_stop=False,
            minimal_roi={},stoploss=-.99,use_exit_signal=True,exit_profit_only=False,ignore_roi_if_entry_signal=False,
            research_root=str(ROOT),fixture_forecasts=str(fpath),fixture_forecasts_sha256=sha(fpath),copula_helper_sha256=sha(ROOT/'freqtrade/data/copula_execution.py'),
            fee=.0015,cache='none',dataformat_ohlcv='feather',export='trades')
        config['exchange']['pair_whitelist']=protocol['pairs']
        config_path=folder/'config.json';save(config_path,config)
        artifacts.append(dict(name=name,**case,config={'path':str(config_path),'sha256':sha(config_path)},markets={'path':str(market_path),'sha256':sha(market_path)},
            datadir=str(folder/'candles'),files={str(p):sha(p)for p in sorted(data.glob('*.feather'))}))
    save(HERE/'fixture_inputs.json',dict(protocol_sha256=sha(HERE/'fixture_protocol.json'),forecasts={'path':str(fpath),'sha256':sha(fpath)},cases=artifacts))
    print('FROZEN6synthetic cases; no source20 financial account admitted')


if __name__=='__main__':main()
