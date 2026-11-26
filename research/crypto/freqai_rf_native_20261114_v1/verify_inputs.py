"""Full raw input grids and exact reproduction of the prior native controls."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent


def main():
    plan=json.loads((HERE/'plan.json').read_text());out=Path(plan['output_root']);directory=Path(plan['datadir'])
    checks=[]
    for tf,kind,freq,start in [('1h','futures','h','2024-11-28T16:00:00Z'),('5m','futures','5min',plan['start']),('1d','futures','D',plan['start']),('1h','mark','h',plan['start']),('1h','funding_rate','8h',plan['start'])]:
        path=directory/'futures'/f'BTC_USDT_USDT-{tf}-{kind}.feather';f=pd.read_feather(path)
        selected=f.loc[f.date.ge(pd.Timestamp(start))&f.date.lt(pd.Timestamp(plan['end']))]
        step=pd.Timedelta(freq if freq[0].isdigit() else '1'+freq)
        expected=pd.date_range(start,pd.Timestamp(plan['end'])-step,freq=freq).as_unit('ns')
        assert pd.DatetimeIndex(selected.date).as_unit('ns').equals(expected)
        assert np.isfinite(selected.select_dtypes('number')).all().all()
        checks.append(dict(type=kind,timeframe=tf,start=start,rows=len(selected),expected_grid_exact=True))
    old=out.parent/'dual_thrust_native_20261114_v1';rows=[]
    for strategy in ['SmaPriceBTC','HoldBTC']:
        for capital in [1000,500]:
            for cost in [15,8]:
                key=f'{strategy}_{capital}_{cost}bps';current=pd.read_parquet(out/key/'hourly_equity.parquet');prior=pd.read_parquet(old/key/'hourly_equity.parquet')
                pd.testing.assert_frame_equal(current,prior,check_exact=True)
                rows.append(dict(account=key,complete_native_frame_exact=True,hours=len(current)))
    (HERE/'input_grid_checks.json').write_text(json.dumps(dict(passed=True,records=checks),indent=2)+'\n')
    (HERE/'control_parity.json').write_text(json.dumps(dict(passed=True,reference_root=str(old),accounts=rows,hour_points=sum(r['hours'] for r in rows),scope='Full hourly native account frames match prior sealed controls; reproduction, not independent OOS'),indent=2)+'\n')
    print('5 raw data grids including training history, and8 native control frames pass')


if __name__=='__main__':main()
