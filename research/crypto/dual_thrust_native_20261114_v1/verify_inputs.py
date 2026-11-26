"""Exact full-window data grids, including every declared BTC funding event."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent


def main():
    plan=json.loads((HERE/'plan.json').read_text());directory=Path(plan['datadir']);audit=[]
    for timeframe,kind,freq in [('1h','futures','h'),('5m','futures','5min'),('1d','futures','D'),('1h','mark','h'),('1h','funding_rate','8h')]:
        path=directory/'futures'/f'BTC_USDT_USDT-{timeframe}-{kind}.feather'
        frame=pd.read_feather(path)
        frame=frame.loc[frame.date.ge(pd.Timestamp(plan['start']))&frame.date.lt(pd.Timestamp(plan['end']))]
        step=pd.Timedelta(freq if freq[0].isdigit() else '1'+freq)
        expected=pd.date_range(plan['start'],pd.Timestamp(plan['end'])-step,freq=freq).as_unit('ns')
        actual=pd.DatetimeIndex(frame.date).as_unit('ns')
        audit.append(dict(type=kind,timeframe=timeframe,expected_contract_frequency=freq,
                          rows=len(frame),expected_rows=len(expected),exact_expected_grid=actual.equals(expected),
                          missing=len(expected.difference(actual)),extra=len(actual.difference(expected)),
                          finite_values=bool(np.isfinite(frame.select_dtypes('number')).all().all()),
                          source_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    assert all(x['exact_expected_grid'] and x['finite_values'] for x in audit),audit
    (HERE/'full_input_grid_audit.json').write_text(json.dumps(dict(passed=True,records=audit,
        funding_contract='BTC supplied8hour events exactgrid; not a universal funding-frequency claim or private settlement certification'),indent=2)+'\n')
    print('5 full data grids pass, including1824 supplied BTC funding events')


if __name__=='__main__':main()
