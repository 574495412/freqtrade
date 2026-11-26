"""Freeze common base quantities and actual native single-wallet configurations."""
import copy
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
OUT=ROOT/'user_data/crypto_quant/paired_carry_source_native_20261115_v1'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def ref(path):return dict(path=str(Path(path).resolve()),sha256=sha(path))
def read(path):return json.loads(Path(path).read_text())
def save(path,obj):Path(path).write_text(json.dumps(obj,indent=2,default=str,allow_nan=False)+'\n')


def main():
    protocol=read(HERE/'protocol.json');assert sha(HERE/'protocol.json')==read(HERE/'protocol_freeze.json')['sha256']
    OUT.mkdir(exist_ok=False);data=OUT/'candles';(data/'futures').mkdir(parents=True)
    spotplan=read(HERE.parent/'spot_baseline_20261105_v003/plan-long.json')
    parent=read(HERE.parent/'source_reversal_adx_native_20261115_v1/plan.json')
    bindings={};checks=[];previous={}
    for symbol in protocol['symbols']:
        for mode in ['spot','futures']:
            pair=symbol+'/USDT'+(':USDT'if mode=='futures'else'')
            files=[('5m','futures'if mode=='futures'else'','5min'),('1h','futures'if mode=='futures'else'','1h')]
            if mode=='futures':files += [('1h','mark','1h'),('1h','funding_rate','8h')]
            for tf,kind,freq in files:
                name=pair.replace('/','_').replace(':','_')+'-'+tf+('-'+kind if kind else'')+'.feather'
                relative=('futures/'if mode=='futures'else'')+name
                original=(Path(parent['datadir'])if mode=='futures'else Path(spotplan['datadir']))/relative
                target=data/relative;os.link(original,target);bindings[relative]=sha(target)
                frame=pd.read_feather(target);index=pd.DatetimeIndex(frame.date)
                assert index.is_unique and index.is_monotonic_increasing
                current=frame.loc[frame.date.ge(pd.Timestamp(protocol['start']))&frame.date.lt(pd.Timestamp(protocol['end']))]
                expected=pd.date_range(protocol['start'],pd.Timestamp(protocol['end'])-pd.Timedelta(freq),freq=freq)
                assert pd.DatetimeIndex(current.date).as_unit('ns').equals(expected.as_unit('ns'))
                assert np.isfinite(current.select_dtypes('number').to_numpy()).all()
                if kind!='funding_rate':assert (current[['open','high','low','close']].to_numpy()>0).all()
                if tf=='5m':previous[pair]=float(frame.set_index('date').loc[pd.Timestamp(protocol['start'])-pd.Timedelta(minutes=5),'close'])
                checks.append(dict(pair=pair,kind=kind or'spot',timeframe=tf,rows=len(current),path=str(target),sha256=sha(target)))
    save(OUT/'data_receipts.json',bindings)
    save(HERE/'input_grid_audit.json',dict(passed=True,records=checks,original_pool=['BTC','ETH'],
        funding_events=sum(r['rows']for r in checks if r['kind']=='funding_rate'),no_missing_expected_source_events=True))
    engine=Path(parent['engine_source']);manifest=read(parent['engine_manifest']['path'])
    for path,digest in manifest['files'].items():assert sha(engine/path)==digest
    sys.path[:0]=[str(engine),str(HERE)]
    name='freqtrade.data.paired_carry';spec=importlib.util.spec_from_file_location(name,ROOT/'freqtrade/data/paired_carry.py');module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    from freqtrade.enums import RunMode
    from freqtrade.resolvers import StrategyResolver
    from freqtrade.data.dataprovider import DataProvider
    from CaptureRules import signal_receipt
    from review_native_limits import native_minimum
    plan=copy.deepcopy(parent)
    plan.update(schema='freqtrade.native.segregated-paired-carry.v1',pairs=list(previous),
        output_root=str(OUT),datadir=str(data),data_receipts=ref(OUT/'data_receipts.json'),marks={},
        candidates=[],total_cost_bps_per_side=[8,13,15,20],allowed_leverage=1.,native_start=protocol['start'],
        price_precision_source=ref(ROOT/'freqtrade/optimize/carry_precision.py'),
        runner_module='research.crypto.paired_carry_source_native_20261115_v1.native_runner',
        runner_sha256=sha(HERE/'native_runner.py'),additional_source_seals=[ref(HERE/'protocol.json'),ref(HERE/'CarryLeg.py'),ref(HERE/'CaptureRules.py'),ref(ROOT/'freqtrade/data/paired_carry.py')])
    markets={m:read(p['market_snapshot']['path'])['markets']for m,p in [('spot',spotplan),('futures',parent)]}
    for pair in previous:
        future=':'in pair;stem=pair.replace('/','_').replace(':','_')
        path=data/('futures'if future else'')/(stem+('-1h-mark'if future else'-1h')+'.feather')
        plan['marks'][pair]=ref(path)
    quantities=[];receipts={};callbacks=0;limit_checks=[]
    for scenario in protocol['cost_scenarios']:
        for capital in protocol['capital_usdt']:
            for role in ['carry','benchmark']:
                for symbol in protocol['symbols']:
                    sp,fp=symbol+'/USDT',symbol+'/USDT:USDT'
                    if role=='carry':
                        wallet=capital/4
                        quantity,step=module.common_quantity(wallet,previous[sp],previous[fp],
                            markets['spot'][sp]['precision']['amount'],markets['futures'][fp]['precision']['amount'],
                            scenario['spot_bps_per_side']/10000,scenario['perp_bps_per_side']/10000)
                    else:
                        wallet=capital/2
                        quantity,step=module.common_quantity(wallet,previous[sp],previous[sp],
                            markets['spot'][sp]['precision']['amount'],markets['spot'][sp]['precision']['amount'],
                            scenario['spot_bps_per_side']/10000,scenario['spot_bps_per_side']/10000)
                    quantities.append(dict(role=role,capital=capital,symbol=symbol,scenario=scenario['name'],
                        leg_capital=wallet,prior_spot_close=previous[sp],prior_perp_close=previous[fp],
                        quantity=str(quantity),common_step=str(step),decision='2025-01-01T00:00:00Z',
                        quantity_input_bar_open='2024-12-31T23:55:00Z'))
                    for mode in (['spot','futures']if role=='carry'else['spot']):
                        pair=fp if mode=='futures'else sp;ownerplan=parent if mode=='futures'else spotplan
                        name=f"{role}_{symbol}_{mode}_{capital}_{scenario['name']}"
                        cfg=read(ownerplan['candidates'][0]['config']['path'])
                        for key in list(cfg):
                            if key.startswith('research_')or key in ['minimal_roi','stoploss','order_types','use_exit_signal','exit_profit_only','ignore_roi_if_entry_signal','trailing_stop','trailing_stop_positive','trailing_stop_positive_offset']:
                                cfg.pop(key)
                        cfg.update(strategy='CarryShort'if mode=='futures'else'CarrySpot',trading_mode=mode,
                            dry_run=True,dry_run_wallet=wallet,max_open_trades=1,stake_amount='unlimited',
                            tradable_balance_ratio=.99,startup_candle_count=1,timeframe='5m',timeframe_detail='',
                            position_adjustment_enable=False,max_entry_position_adjustment=0,research_carry_leg=True,
                            research_quantity=float(quantity))
                        if mode=='spot':cfg.pop('margin_mode',None)
                        else:cfg['margin_mode']='isolated'
                        cfg['exchange']['pair_whitelist']=[pair]
                        cfg['exchange'].get('ccxt_config',{}).pop('options',None)
                        cfg['exchange'].get('ccxt_async_config',{}).pop('options',None)
                        path=HERE/(name+'.json');save(path,cfg)
                        candidate=dict(name=name,strategy=cfg['strategy'],capital=wallet,portfolio_capital=capital,
                            role=role,symbol=symbol,mode=mode,scenario=scenario['name'],quantity=float(quantity),
                            cost_bps=scenario['perp_bps_per_side'if mode=='futures'else'spot_bps_per_side'],pairs=[pair],
                            config=ref(path),market_snapshot=ownerplan['market_snapshot'],observer_source=ownerplan['observer_source'],
                            source_files={'CarryLeg.py':ref(HERE/'CarryLeg.py')})
                        plan['candidates'].append(candidate)
                        test=copy.deepcopy(cfg);test.update(runmode=RunMode.BACKTEST,datadir=data,
                            user_data_dir=ROOT/'user_data',strategy_path=str(HERE))
                        owner=StrategyResolver.load_strategy(test);owner.dp=DataProvider(test,None);owner.bot_start()
                        raw=owner.dp.get_pair_dataframe(pair,'5m','futures'if mode=='futures'else'spot')
                        actual=owner.ft_advise_signals(owner.advise_indicators(raw.copy(),{'pair':pair}),{'pair':pair})
                        receipts[name]=signal_receipt(actual,cfg['strategy'])
                        assert actual.loc[actual.date.ge(pd.Timestamp(protocol['start'])),'enter_short'if mode=='futures'else'enter_long'].sum()==1
                        budget=wallet*.99
                        # Real callback uses actual native budgets; this preflight checks the known intent only.
                        minimum=native_minimum(test,markets[mode],pair,previous[pair])
                        requested=float(quantity)*previous[pair]
                        admitted=minimum is not None and minimum<=requested<=budget
                        observed=owner.custom_stake_amount(pair,pd.Timestamp('2025-01-01T00:05Z'),previous[pair],budget,
                            minimum,budget,1,None,'short'if mode=='futures'else'long')
                        assert (observed>0)==admitted
                        if not admitted:assert owner._fatal_error
                        limit_checks.append(dict(candidate=name,admitted=admitted,native_minimum=minimum,requested=requested))
                        callbacks+=1
    assert len(plan['candidates'])==24
    save(HERE/'quantity_intents.json',quantities);save(HERE/'preflight_signal_receipts.json',receipts)
    save(HERE/'plan.json',plan);save(HERE/'preflight.json',dict(passed=all(r['admitted']for r in limit_checks),native_limit_checks=limit_checks,plan=ref(HERE/'plan.json'),
        native_original_files=595,declared_leg_accounts=24,actual_native_callback_checks=callbacks,
        complete_input_files=len(checks),financial_accounts_started=False,source_strategy_reproduction=False))
    print('Prepared24nativelegwallets;12completeinputs;24nativequantitycallbacks;0financialaccountsstarted')


if __name__=='__main__':main()
