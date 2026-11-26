"""Freeze source-faithful strategy configs and native OHLCV input preparation."""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE=Path(__file__).absolute().parent
ROOT=HERE.parents[2]
PARENT=HERE.with_name('mama_phase_source_native_20261116_v1')


def record(path):
    path=Path(path)
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def save(path,value):
    Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def main():
    if (HERE/'plan.json').exists():
        raise ValueError('Keep original plan')
    old=json.loads((PARENT/'plan.json').read_text())
    plan=json.loads(json.dumps(old))
    out=ROOT/'user_data/crypto_quant'/HERE.name
    candles=out/'candles'
    candles.mkdir(parents=True,exist_ok=False)
    hashes,provenance,coverage={},{},[]
    original_hashes=json.loads((PARENT/'data_receipts.json').read_text())
    for name,digest in original_hashes.items():
        source=Path(old['datadir'])/name
        assert record(source)['sha256']==digest
        target=candles/name
        os.link(source,target)
        hashes[name]=digest
        provenance[name]={'source':str(source),'sha256':digest,'same_inode':target.stat().st_ino==source.stat().st_ino}
    for coin in ['BTC','ETH']:
        f=pd.read_feather(candles/(coin+'_USDT-5m.feather'))
        dates=pd.DatetimeIndex(f.date).as_unit('ns')
        assert dates.is_unique and dates.is_monotonic_increasing
        assert dates.equals(pd.date_range(dates[0],dates[-1],freq='5min').as_unit('ns'))
        assert np.isfinite(f[['open','high','low','close','volume']].to_numpy()).all()
        assert f[['open','high','low','close']].gt(0).all().all() and f.volume.ge(0).all()
        grouped=f.set_index('date').resample('4h',origin='start_day',closed='left',label='left')
        assert grouped.size().eq(48).all()
        bars=grouped.agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).reset_index()
        assert bars.volume.gt(0).all()
        target=candles/(coin+'_USDT-4h.feather')
        bars.to_feather(target)
        hashes[target.name]=record(target)['sha256']
        main=bars.loc[bars.date.between(pd.Timestamp('2025-01-01',tz='UTC'),pd.Timestamp('2026-08-31T20:00Z'))]
        assert len(main)==3648
        hourly=pd.read_feather(candles/(coin+'_USDT-1h.feather')).set_index('date')
        np.testing.assert_array_equal(main.open,hourly.open.reindex(pd.DatetimeIndex(main.date)))
        age=ROOT/'user_data/crypto_quant/spot_baseline_data_long_20261105_v001/candles'/(coin+'_USDT-1d.feather')
        age_frame=pd.read_feather(age)
        assert age_frame.date.iloc[0] <= pd.Timestamp('2025-01-01',tz='UTC')-pd.Timedelta(days=100)
        coverage.append({'pair':coin+'/USDT','source5m_rows':len(f),'prepared4h_rows':len(bars),
                         'target4h_rows':len(main),'earlier_observation':str(age_frame.date.iloc[0]),
                         'age_reference':record(age),'historical_listing_certificate':False,
                         'earlier_observation_is_not_relisting_or_PIT_certificate':True})
    save(HERE/'data_receipts.json',hashes)
    save(HERE/'hardlink_provenance.json',provenance)
    save(HERE/'input_review.json',{'coverage':coverage,'resampling':'Exact48 original5m bars per4h,labelleft UTC',
                                 'source_prices_or_volume_imputed':False,'target_window_unchanged':True})
    plan.update(registered_at=datetime.now(UTC).isoformat(),output_root=str(out),
                datadir=str(candles),native_start='2024-12-31T20:00:00Z',
                schema='heracles-source-native-v1',runner_module='research.crypto.'+HERE.name+'.native_runner',
                runner_sha256=record(HERE/'native_runner.py')['sha256'],
                data_receipts=record(HERE/'data_receipts.json'),memory_guard_bytes=2*1024**3,
                wall_guard_seconds=1800)
    template=json.loads(Path(old['candidates'][0]['config']['path']).read_text())
    candidates=[]
    for capital in [1000,500]:
        for scenario,cost in [('conservative',20),('lower',13)]:
            name=f'heracles_{capital}_{scenario}'
            config=json.loads(json.dumps(template))
            for key in ['research_mama_phase','research_month_boundary','research_universal_allocation',
                        'allocation_start_day','allocation_end_day']:
                config.pop(key,None)
            config.update(strategy='Heracles',timeframe='4h',startup_candle_count=100,
                          dry_run_wallet=capital,bot_name=name,fee=cost/10000,
                          position_adjustment_enable=False,max_entry_position_adjustment=0)
            path=HERE/(name+'.json')
            save(path,config)
            candidates.append({'name':name,'strategy':'Heracles','capital':capital,'mode':'spot',
                               'scenario':scenario,'cost_bps':cost,'pairs':old['pairs'],
                               'config':record(path),'source_files':{'Heracles.py':record(HERE/'sources/Heracles.py')},
                               'market_snapshot':old['candidates'][0]['market_snapshot'],
                               'observer_source':old['candidates'][0]['observer_source']})
    plan['candidates']=candidates
    paths=[HERE/p for p in ['protocol.json','terminal_contract.json','run.py','CaptureRules.py',
                            'source_receipts.json','ta_package_receipt.json','ta_dependency_files.json',
                            'input_review.json','ownership.json']]
    paths += list((HERE/'dependencies/ta').glob('*.py'))
    paths += [ROOT/'research/crypto/band_trend_native_20261113_v1/observe_clock.py',
              ROOT/'research/crypto/skew_vol_native_20261113_v1/clock_fix.py']
    plan['additional_source_seals']=[record(p) for p in paths]
    save(HERE/'plan.json',plan)
    print('Prepared4nativewallet configs,6datafiles;0financial starts',flush=True)


if __name__=='__main__':
    main()
