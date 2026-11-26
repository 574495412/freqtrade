"""Audit actual raw quotes and native provider input paths; no funded start."""
from datetime import datetime
import hashlib
import inspect
import json
from pathlib import Path

import pandas as pd

from bootstrap import boot

HERE=Path(__file__).absolute().parent


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    engine=boot()
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.data.macro_transmission import MacroClock,parse_yahoo_macro
    from freqtrade.data.macro_transmission_provider import MacroTransmissionDataProvider
    from freqtrade.enums import RunMode
    receipts=json.loads((HERE/'macro_http_receipts.json').read_text())
    records=[];snapshots=[]
    for row,symbol in zip(receipts,['^TNX','DX-Y.NYB'],strict=True):
        assert row['status']==200 and sha(row['path'])==row['sha256']
        snapshot=parse_yahoo_macro(Path(row['path']).read_bytes(),symbol=symbol,
            observed_at=datetime.fromisoformat(row['observed_at']))
        snapshots.append(snapshot)
        records.append(dict(symbol=symbol,raw_rows=len(snapshot.quotes),
            source_timezone=snapshot.timezone,null_rows=sum(q.close is None for q in snapshot.quotes),
            first_day=str(snapshot.quotes[0].session_day),last_day=str(snapshot.quotes[-1].session_day),
            observed_at=row['observed_at'],source_sha256=row['sha256']))
    config={'runmode':RunMode.BACKTEST,'research_macro_inputs':True,'timeframe':'1h'}
    providers=[MacroTransmissionDataProvider(config,*snapshots,clock=clock) for clock in MacroClock]
    grid=pd.date_range('2025-01-01T00:00Z','2026-08-31T23:00Z',freq='h')
    audits=[]
    for mode,provider in zip(MacroClock,providers,strict=True):
        rows=[]
        for at in grid:
            provider._set_dataframe_max_date(at)
            result=provider.macro_headwind()
            rows.append(dict(clock=at,status=result['status'],headwind=result['headwind'],
                target_fraction=result['target_fraction']))
        frame=pd.DataFrame(rows);frame.to_parquet(HERE/(mode.value+'_hourly_inputs.parquet'),index=False)
        known=frame.target_fraction.notna()
        audits.append(dict(mode=mode.value,actual_native_callback_clocks=len(frame),
            unavailable=int((~known).sum()),known_or_model_only=int(known.sum()),
            historical_first_seen_certified=False,
            clock_enforces_snapshot_observation=mode==MacroClock.SNAPSHOT_OBSERVED,
            actual_snapshot_observed_before_decision_clocks=int(sum(at >= max(s.observed_at for s in snapshots) for at in grid)),
            model_only=mode==MacroClock.NEXT_DAY_06UTC_MODEL))
    assert audits[0]['unavailable']==14592
    # Current snapshot and the author's original fixture are separate versions.
    fixture=pd.read_parquet(HERE/'sources/10_cross_asset.parquet')
    comparisons=[]
    for snapshot,col in zip(snapshots,['UST10Y','DXY'],strict=True):
        rows=[]
        for q in snapshot.quotes:
            stamp=pd.Timestamp(q.session_day)
            if q.close is None or stamp not in fixture.index:continue
            old=float(fixture.loc[stamp,col]);difference=abs(float(q.close)-old)
            rows.append(dict(day=str(q.session_day),current_quote=q.close,fixture_quote=old,difference=difference))
        comparisons.append(dict(symbol=snapshot.symbol,compared_original_quote_rows=len(rows),
            changed_rows=sum(r['difference']>1e-12 for r in rows),
            max_difference=max((r['difference'] for r in rows),default=None),rows=rows))
    result=dict(passed=True,evidence='inputtechnicalonly',engine=engine,
        native_provider_path=inspect.getfile(DataProvider),native_subclass=issubclass(MacroTransmissionDataProvider,DataProvider),
        source_rows=records,total_raw_rows=sum(r['raw_rows'] for r in records),
        total_original_null_rows=sum(r['null_rows'] for r in records),original_null_rows_not_filled=True,
        mode_audits=audits,fixture_version_comparison=comparisons,new_funded_accounts=0,
        factor_analysis=0,bench=0,source_author_return_adopted=False,orders_enabled=False)
    assert result['native_provider_path'].startswith('/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade/')
    (HERE/'input_audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(rows=result['total_raw_rows'],nulls=result['total_original_null_rows'],modes=audits)))


if __name__=='__main__':main()
