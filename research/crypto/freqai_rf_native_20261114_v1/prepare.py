"""Native FreqAI component preflight before financial registration."""
import copy
import hashlib
import json
import os
import sys
from pathlib import Path

import pandas as pd
from bootstrap import boot

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
OUT=ROOT/'user_data/crypto_quant/freqai_rf_native_20261114_v1'


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def ref(p):return dict(path=str(Path(p).resolve()),sha256=sha(p))
def save(p,v):Path(p).write_text(json.dumps(v,indent=2,default=str)+'\n')


def prepare_inputs():
    OUT.mkdir(exist_ok=True);directory=OUT/'candles';(directory/'futures').mkdir(parents=True,exist_ok=True)
    previous=ROOT/'user_data/crypto_quant/dual_thrust_native_20261114_v1'
    receipts=json.loads((previous/'data_receipts.json').read_text())
    for name,digest in receipts.items():
        assert sha(previous/'candles'/name)==digest
        if not (directory/name).exists():os.link(previous/'candles'/name,directory/name)
        assert sha(directory/name)==digest
    save(OUT/'data_receipts.json',receipts)
    from bootstrap import ENGINE
    sources=[(ENGINE/'freqtrade/templates/FreqaiExampleStrategy.py',HERE/'FreqaiExampleStrategy.py')]
    sources += [(HERE.parent/'dual_thrust_native_20261114_v1'/n,HERE/n) for n in ['DualPortfolio.py','DualRules.py']]
    for source,target in sources:
        if target.exists():assert sha(source)==sha(target)
        else:target.write_bytes(source.read_bytes())


def config_for(strategy,capital):
    config=json.loads((HERE.parent/'dual_thrust_native_20261114_v1/SmaPriceBTC_1000.json').read_text())
    config.update(strategy=strategy,dry_run_wallet=capital,startup_candle_count=80 if strategy.startswith('Rolling') else 600)
    if strategy.startswith('Rolling'):
        config.update(freqaimodel='AuditedRF' if strategy=='RollingRF' else 'TrainingPrior',freqaimodel_path=str(HERE))
        config['freqai']=dict(enabled=True,identifier='rolling_rf_v1' if strategy=='RollingRF' else 'rolling_prior_v1',
            train_period_days=30,backtest_period_days=7,save_backtest_models=False,purge_old_models=0,
            activate_tensorboard=False,data_kitchen_thread_count=1,continual_learning=False,
            feature_parameters=dict(include_timeframes=['1h'],include_corr_pairlist=[],indicator_periods_candles=[10,20],
                include_shifted_candles=2,label_period_candles=20,DI_threshold=0,weight_factor=0,
                principal_component_analysis=False,use_SVM_to_remove_outliers=False,use_DBSCAN_to_remove_outliers=False,
                noise_standard_deviation=0,shuffle_after_split=False,buffer_train_data_candles=0,plot_feature_importances=0),
            data_split_parameters=dict(test_size=0,shuffle=False),
            model_training_parameters=dict(n_estimators=100,max_depth=5,min_samples_leaf=5,max_features='sqrt',n_jobs=1,random_state=42))
    return config


def native_component(strategy,start,variant,raw_override=None):
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.enums import RunMode
    from freqtrade.resolvers import ExchangeResolver,StrategyResolver
    stop=start+pd.Timedelta(days=7)
    tag=f'{strategy}_{start.strftime("%Y%m%d")}_{variant}';destination=OUT/'components'/tag
    destination.mkdir(parents=True,exist_ok=False)
    raw=config_for(strategy,1000)
    raw.update(user_data_dir=str(destination),datadir=str(OUT/'candles'),strategy_path=str(HERE),
               research_scope_start=str(start),research_scope_end=str(stop-pd.Timedelta(hours=1)),research_component_only=True,
               timerange=f'{int(start.timestamp())}-{int(stop.timestamp())-1}')
    path=destination/'config.json';save(path,raw)
    config=setup_optimize_configuration({'config':[str(path)],'datadir':OUT/'candles'},RunMode.BACKTEST)
    owner=StrategyResolver.load_strategy(config)
    exchange=ExchangeResolver.load_exchange(config,validate=False,load_leverage_tiers=False)
    parent=json.loads((HERE.parent/'dual_thrust_native_20261114_v1/plan.json').read_text())
    snapshot=json.loads(Path(parent['market_snapshot']['path']).read_text())
    try:
        exchange._markets=snapshot['markets'];exchange._leverage_tiers=snapshot['leverage_tiers']
        exchange._api.set_markets(snapshot['markets'],snapshot['currencies']);exchange._api_async.set_markets(snapshot['markets'],snapshot['currencies'])
        exchange.validate_config(config)
        owner.dp=DataProvider(config,exchange);owner.ft_bot_start()
        frame=owner.dp.get_pair_dataframe('BTC/USDT:USDT','1h','futures')
        if raw_override is not None:frame=raw_override(frame)
        result=owner.advise_indicators(frame.copy(),{'pair':'BTC/USDT:USDT'})
        records=[json.loads(p.read_text()) for p in (destination/'freqai_audit').glob('model_*.json')]
        assert len(records)==1 and records[0]['mature_label_check_passed']
        assert records[0]['independent_prediction_reconciliation_passed']
        return result,records[0]
    finally:
        owner.ft_bot_cleanup();exchange.close()


def main():
    assert sha(HERE/'protocol.json')==json.loads((HERE/'protocol_freeze.json').read_text())['sha256']
    manifest=boot();prepare_inputs();checks=[]
    for strategy in ['RollingRF','RollingPrior']:
        for start in pd.to_datetime(['2025-01-01','2025-06-01','2026-01-01'],utc=True):
            original,record=native_component(strategy,start,'original')
            cutoff=start+pd.Timedelta(days=3)
            def change(frame):
                frame=frame.copy();frame.loc[frame.date.ge(cutoff),['open','high','low','close','volume']]*=11
                return frame
            for variant,modify in [('future',change),('truncate',lambda frame:frame.loc[frame.date.lt(cutoff)].copy())]:
                altered,_=native_component(strategy,start,variant,modify)
                cols=['date','&-direction','up','down','do_predict','target_weight','source_signal']
                pd.testing.assert_frame_equal(original.loc[original.date.lt(cutoff),cols].reset_index(drop=True),altered.loc[altered.date.lt(cutoff),cols].reset_index(drop=True),check_exact=True)
            checks.append(dict(strategy=strategy,start=str(start),actual_native_refits=3,
                               prefix_future_and_truncation_passed=True,original_native_model=record))
            print('PREFLIGHT',strategy,start,'passed',flush=True)
    save(HERE/'preflight.json',dict(passed=True,checks=checks,actual_native_component_model_fits=18,
         native_source_files_verified=len(manifest['files']),financial_accounts=0,protocol=ref(HERE/'protocol.json')))
    print('Native FreqAI preflight passed',flush=True)


if __name__=='__main__':main()
