"""Freeze local transfer and bind immutable native prices/funding, no returns."""
import json
import os
from datetime import UTC,datetime
from pathlib import Path

import pandas as pd

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save,sha

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
OUT=ROOT/'user_data/crypto_quant/tsmom_risk_native_20261114_v1'


def ref(path):return {'path':str(path),'sha256':sha(path)}


def main():
    if (HERE/'input_admission.json').exists():raise FileExistsError('Frozen preparation already exists')
    flow=json.loads((HERE.parent/'taker_flow_native_20261114_v1/input_admission_v2.json').read_text())
    prior=json.loads((HERE.parent/'funding_cross_section_native_20261114_v1/input_admission.json').read_text())
    pairs=prior['active_pairs'];old=Path(prior['funding_events']['path']).parent/'candles/futures'
    new=Path(flow['native_datadir'])/'futures'
    (OUT/'candles/futures').mkdir(parents=True,exist_ok=True);records=[]
    protocol={'frozen_at_utc':datetime.now(UTC).isoformat(),'before_native_account_outputs':True,
        'source_manifest':ref(HERE/'source_manifest.json'),'source_is_aqr_official':False,
        'hypothesis':'Source30/90/365trend+inversevol; target20%vol via .95sample+.05diagonal covariance vs diagonal-only and gross1 controls',
        'source_parameters':{'target_vol':.2,'max_cov_days':1095,'shrinkage':.05,'max_weight':.5,'max_gross':3.,'kill_switch':False},
        'mode_contract':'Source self-managed net-of-trading-costs option; enable_fee_simulation=False. No external manager2/20 fee. Not source default fund-fee performance replication.',
        'local_pool':pairs,'pool_limits':'Full prior29fixedpool,not source532pool or PIT eligibility. Top100 is non-binding for29. No post-result exclusions.',
        'price_support':'Joint observed daily support begins at latest first available local daily input. Actual partial covariance up to1095days,not certified3y on first trade. No missing price fill.',
        'timing':'Preserve actual source extra-bar implementation: nativeT01 uses source signal/vol closeT-2,covreturns strictly beforeT-2. Source30/90/365 periods unchanged.',
        'native_execution':'Daily target quantities at01:00 via native entries/full exits/adjust_trade_position; native quantity precision and min/maxstake,under-min target stays cash,small delta remains tracking error; no free notional reset',
        'sizing':'Snapshot actual native filled-order marked equity before daily orders;99%budget;targetnotional/actualfuturesOPEN. Actual nativewallet clipscash;3x crossmargin constant for all3models.',
        'comparison':'2025Jan1 00--2026Aug31 23UTC,1000/500 wallets,15/8bps per-side aggregatecost. Combo only actualCov500+GrossOne500,clocks identical,no transfers/scaled1000paths.',
        'funding':'Supplied native events only;TIA2026Jun24 04UNKNOWN retained. Complete modeled accounts,not complete actual economic settlement; exposure/gap adjudicated peractualholdings.',
        'numeric_gate':'positive full andbothcontinuousyear slices,DD<=50%,Cov better than bothcontrols at bothcosts;combo beats bestsamecapitalstandalone atbothcosts. Numericpass not qualification without data/OOS.',
        'no_rescue':'No lookbacks/source-lag/sign/covshrink/universe/cost/weights changes after outcomes',
        'orders_enabled':False,'production_eligible':False}
    save(HERE/'protocol.json',protocol)
    for pair in pairs:
        sym=pair.split('/')[0]
        for suffix,base in [('1h-futures',new),('5m-futures',new),('1h-mark',old),('1h-funding_rate',old)]:
            source=base/f'{sym}_USDT_USDT-{suffix}.feather';target=OUT/'candles/futures'/source.name
            os.link(source,target);records.append({'source':str(source),'target':str(target),'sha256':sha(source)})
        original=pd.read_feather(old/f'{sym}_USDT_USDT-1d-futures.feather')
        hourly=pd.read_feather(new/f'{sym}_USDT_USDT-1h-futures.feather')
        daily=hourly.set_index('date').resample('D').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).reset_index()
        frame=pd.concat([original.loc[original.date<daily.date.iloc[0],['date','open','high','low','close','volume']],daily],ignore_index=True)
        if frame.date.duplicated().any():raise ValueError('Daily splice duplicate')
        target=OUT/f'candles/futures/{sym}_USDT_USDT-1d-futures.feather';frame.to_feather(target)
        records.append({'source':str(old/f'{sym}_USDT_USDT-1d-futures.feather'),'target':str(target),'sha256':sha(target),'daily_splice':'original before2024Dec1,canonical5m-derived thereafter'})
    hashes={str(p.relative_to(OUT/'candles')):sha(p) for p in sorted((OUT/'candles/futures').glob('*.feather'))}
    save(OUT/'data_receipts.json',hashes)
    metadata=ROOT/'user_data/crypto_quant/nfi80_historical_universe_20261109_v001/market-rules.json'
    snap=json.loads(metadata.read_text())
    if any(p not in snap['markets'] or p not in snap['leverage_tiers'] for p in pairs):raise ValueError('Missing frozen margin metadata')
    save(HERE/'input_admission.json',{'price_inputs_bound':True,'financial_settlement_certified':False,'pairs':pairs,
        'source_links':records,'datadir':str(OUT/'candles'),'data_receipts':ref(OUT/'data_receipts.json'),
        'market_snapshot':ref(metadata),'funding_limit':ref(HERE.parent/'funding_cross_section_native_20261114_v1/input_admission.json'),
        'old_data_changed':False})
    print('PREPARED29pairs145nativefiles; funding actualsettlement remains uncertified')


if __name__=='__main__':main()
