"""Actual snapshot-clock evidence reconciliation; not publication certification."""
import json
import sys
from datetime import UTC,datetime
from pathlib import Path

import pandas as pd
from prepare import sha,ref,save

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2];V2=HERE/'v002'
OUT=ROOT/'user_data/crypto_quant/onchain_hash_native_20261114_v1/v002'


def main():
    if not (V2/'protocol_freeze.json').exists():
        save(V2/'protocol_freeze.json',dict(utc=datetime.now(UTC).isoformat(),protocol=ref(V2/'protocol.json'),before_feature_states=True,before_any_financial_runs=True))
    else:assert sha(V2/'protocol.json')==json.loads((V2/'protocol_freeze.json').read_text())['protocol']['sha256']
    owner=ROOT/'freqtrade/data/onchain.py';(HERE/'OnchainInputs.py').write_bytes(owner.read_bytes())
    sys.path.insert(0,str(HERE))
    from OnchainInputs import DailyHashSource,MetricClock,reconcile_hash_completion_snapshots
    current=pd.DataFrame(json.loads((HERE/'sources/community_api.json').read_text())['data']).rename(columns={'time':'date'})
    archived=pd.read_csv(HERE/'sources/btc.csv',dtype=str,keep_default_na=False).rename(columns={'time':'date'})
    raw=reconcile_hash_completion_snapshots(current,archived)
    raw.to_parquet(OUT/'hash_daily.parquet',index=False)
    receipt=json.loads((HERE/'sources/community_api_receipt.json').read_text());source=DailyHashSource(raw,pd.Timestamp(receipt['captured_at']))
    hours=pd.date_range('2025-01-01T00:00Z','2026-08-31T23:00Z',freq='h')
    checks=[]
    for mode,lag in [(MetricClock.SNAPSHOT_OBSERVED,0),(MetricClock.COMPLETION_MODEL,0),(MetricClock.COMPLETION_MODEL,24)]:
        mapped=source.asof_hours(hours,clock=mode,delay_hours=lag,max_age_hours=72)
        mapped.to_parquet(OUT/f'input_clock_{mode}_{lag}.parquet',index=False)
        checks.append(dict(clock=str(mode),delay_hours=lag,status_counts=mapped.status.value_counts().to_dict(),
                           maximum_age_hours=float(mapped.age_hours.max()) if mapped.age_hours.notna().any() else None,
                           observed_before_decision_hours=int(mapped.snapshot_observed_before_decision.sum())))
    restored=raw.loc[raw.current_completion_missing,['date','HashRate','completion_current','completion_archived','AssetEODCompletionTime']]
    features=source.features(MetricClock.COMPLETION_MODEL).set_index('metric_day')
    decisions=pd.date_range('2025-01-01','2026-08-31',freq='D',tz='UTC')+pd.Timedelta(hours=1)
    expected_day=decisions.floor('D')-pd.Timedelta(days=1)
    at01=features.available_at.reindex(expected_day).to_numpy()>decisions.to_numpy()
    conditional=[r for r in checks if r['clock']=='completion_model']
    save(V2/'input_admission.json',dict(parsed_snapshot_rows=700,current_completion_missing_rows=int(raw.current_completion_missing.sum()),
        same_value_archive_clock_rows=int(raw.completion_archived.notna().sum()),original_missing_preserved=True,
        restored_actual_provider_clock_records=restored.astype(str).to_dict('records'),strict_historical_pit_admitted=False,
        conditional_completion_models_complete=all(r['status_counts']=={'KNOWN':14592} for r in conditional),
        prior_day_not_ready_at01_days=int(at01.sum()),prior_day01_decisions=len(decisions),clocks=checks,
        source_api=ref(HERE/'sources/community_api.json'),source_archive=ref(HERE/'sources/btc.csv'),
        source_receipt=ref(HERE/'sources/community_api_receipt.json'),metrics=ref(OUT/'hash_daily.parquet'),
        owner_module=ref(owner),native_input_copy=ref(HERE/'OnchainInputs.py'),parent_failure=ref(HERE/'initial_input_failure.json'),
        rule='Maximum of actually reported completion times for exact same raw HashRate; no interpolation or current-null overwrite',
        caveat='Only conditional calculation-time models; no proof of original historical value version or release.'))
    print(json.dumps(dict(clocks=checks,prior_day_not_ready_at01=int(at01.sum()),restored_dates=restored.date.astype(str).tolist())),flush=True)


if __name__=='__main__':main()
