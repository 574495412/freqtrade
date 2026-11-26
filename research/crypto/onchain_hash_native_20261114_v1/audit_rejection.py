"""Independent input-clock and actual native strategy rejection evidence."""

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

HERE=Path(__file__).resolve().parent; ROOT=HERE.parents[2]
OUT=ROOT/'user_data/crypto_quant/onchain_hash_native_20261114_v1/v002'


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):Path(p).write_text(json.dumps(v,indent=2,default=str)+'\n')


def main():
    parent=json.loads((HERE.parent/'tsmom_risk_native_20261114_v1/v002/plan.json').read_text())
    manifest=json.loads(Path(parent['engine_manifest']['path']).read_text());engine=Path(parent['engine_source'])
    for path,digest in manifest['files'].items():assert sha(engine/path)==digest,path
    sys.path.insert(0,str(engine));sys.path.insert(0,str(HERE))
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.enums import RunMode
    from freqtrade.resolvers import ExchangeResolver,StrategyResolver
    from OnchainInputs import OnchainInputError
    source_receipt=json.loads((HERE/'sources/community_api_receipt.json').read_text())
    current=json.loads((HERE/'sources/community_api.json').read_text())['data']
    assert sha(HERE/'sources/community_api.json')==source_receipt['sha256']
    raw=pd.read_parquet(OUT/'hash_daily.parquet')
    assert len(raw)==700 and raw.date.is_unique and len(current)==700
    episodes=[]
    for mode,lag in [('snapshot_observed',0),('completion_model',0),('completion_model',24)]:
        f=pd.read_parquet(OUT/f'input_clock_{mode}_{lag}.parquet')
        bad=f.loc[f.status.ne('KNOWN')]
        groups=bad.clock.diff().ne(pd.Timedelta(hours=1)).cumsum()
        parts=[dict(start=str(p.clock.iloc[0]),end=str(p.clock.iloc[-1]),hours=len(p),
                    maximum_age_hours=float(p.age_hours.max()) if p.age_hours.notna().any() else None)
               for _,p in bad.groupby(groups)]
        episodes.append(dict(clock=mode,lag_hours=lag,bad_hours=len(bad),episodes=parts))
    snapshot=json.loads(Path(parent['market_snapshot']['path']).read_text())
    base=json.loads((HERE.parent/'pairs_cointegration_native_20261114_v1/TrendGrossOne_1000.json').read_text())
    base.update(timeframe='1h',strategy_path=str(HERE),datadir=OUT/'candles',
                user_data_dir=ROOT/'user_data',runmode=RunMode.BACKTEST,
                timerange='1735689600-1788217200',max_open_trades=1,
                position_adjustment_enable=False,margin_mode='isolated',
                research_metric_root=str(OUT),research_metric_api=str(HERE/'sources/community_api.json'),
                research_decision_end='2026-08-31T23:00:00Z',
                research_metric_archive=str(HERE/'sources/btc.csv'),
                research_metric_receipt=str(HERE/'sources/community_api_receipt.json'))
    base['exchange']['pair_whitelist']=['BTC/USDT:USDT']
    checks=[]
    for name,clock,lag in [('HashOnly','snapshot_observed',0),('HashOnly','completion_model',0),
                          ('HashAndPrice','completion_model',0),('PriceAtHashClock','completion_model',0),
                          ('HashAndPriceDelay24','completion_model',24),('PriceAtHashClockDelay24','completion_model',24)]:
        cfg={**base,'strategy':name,'research_metric_clock':clock,'research_allow_completion_model':clock=='completion_model'}
        strategy=StrategyResolver.load_strategy(cfg)
        exchange=ExchangeResolver.load_exchange(cfg,validate=False,load_leverage_tiers=False)
        try:
            exchange._markets=snapshot['markets'];exchange._leverage_tiers=snapshot['leverage_tiers']
            exchange._api.set_markets(snapshot['markets'],snapshot['currencies'])
            exchange._api_async.set_markets(snapshot['markets'],snapshot['currencies'])
            strategy.dp=DataProvider(cfg,exchange)
            try:strategy.bot_start()
            except OnchainInputError as exc:
                checks.append(dict(strategy=name,clock=clock,lag_hours=lag,status='REJECTED_BEFORE_STATES_AND_ACCOUNT',reason=str(exc)))
            else:raise AssertionError('Unadmitted chain reached strategy states')
        finally:exchange.close()
    assert len(checks)==6
    result=dict(native_commit=manifest['commit'],native_source_files_verified=len(manifest['files']),
                raw_metric_rows=700,strict_historical_pit_admitted=False,
                native_strategy_resolver=True,native_data_provider=True,
                raw_api_sha256=sha(HERE/'sources/community_api.json'),
                native_clock_owner_sha256=sha(ROOT/'freqtrade/data/onchain.py'),
                same_value_clock_reconciliation=True,episodes=episodes,native_rejections=checks,
                planned_accounts=28,executed_financial_accounts=0,accepted_new_factors=0,
                financial_metrics=None,objective_achieved=False,no_gate_relaxation=True)
    save(HERE/'v002/rejection_audit.json',result)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
