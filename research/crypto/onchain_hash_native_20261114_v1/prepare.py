"""Bind the current metric snapshot and immutable existing execution inputs."""
import hashlib
import importlib.util
import json
import os
import sys
from datetime import UTC,datetime
from pathlib import Path

import pandas as pd

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
OUT=ROOT/'user_data/crypto_quant/onchain_hash_native_20261114_v1'
ENGINE=Path('/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def ref(path):return dict(path=str(Path(path).resolve()),sha256=sha(path))
def save(path,value):Path(path).write_text(json.dumps(value,indent=2,default=str)+'\n')


def main():
    if not (HERE/'protocol_freeze.json').exists():
        save(HERE/'protocol_freeze.json',dict(utc=datetime.now(UTC).isoformat(),protocol=ref(HERE/'protocol.json'),before_feature_states=True,before_any_financial_run=True))
    else:assert sha(HERE/'protocol.json')==json.loads((HERE/'protocol_freeze.json').read_text())['protocol']['sha256']
    OUT.mkdir(exist_ok=True,parents=True);directory=OUT/'candles';(directory/'futures').mkdir(exist_ok=True,parents=True)
    old=ROOT/'user_data/crypto_quant/pairs_cointegration_native_20261114_v1'
    receipts=json.loads((old/'data_receipts.json').read_text());new={};links=[]
    for relative,digest in receipts.items():
        if not Path(relative).name.startswith(('BTC_USDT_USDT-','BTC_USDT-')):continue
        source=old/'candles'/relative;target=directory/relative
        assert sha(source)==digest
        if not target.exists():os.link(source,target)
        assert sha(target)==digest
        new[relative]=digest;links.append(dict(source=str(source),target=str(target),sha256=digest))
    assert len(new)==6
    save(OUT/'data_receipts.json',new)
    payload=json.loads((HERE/'sources/community_api.json').read_text())
    assert set(payload)=={'data'} and len(payload['data'])==700
    records=payload['data'];assert all(r['asset']=='btc' for r in records)
    frame=pd.DataFrame(records).rename(columns={'time':'date'})
    frame['date']=pd.to_datetime(frame.date,utc=True)
    frame=frame[['date','HashRate','AssetEODCompletionTime']]
    frame[['HashRate','AssetEODCompletionTime']]=frame[['HashRate','AssetEODCompletionTime']].astype(float)
    grid=pd.date_range('2024-10-01','2026-08-31',freq='D',tz='UTC')
    assert pd.DatetimeIndex(frame.date).equals(grid)
    frame.to_parquet(OUT/'hash_daily.parquet',index=False)
    owner=ROOT/'freqtrade/data/onchain.py';(HERE/'OnchainInputs.py').write_bytes(owner.read_bytes())
    sys.path.insert(0,str(HERE));from OnchainInputs import DailyHashSource,MetricClock
    receipt=json.loads((HERE/'sources/community_api_receipt.json').read_text());observed=pd.Timestamp(receipt['captured_at'])
    source=DailyHashSource(frame,observed)
    hours=pd.date_range('2025-01-01T00:00Z','2026-08-31T23:00Z',freq='h')
    diagnostics=[]
    for mode,lag in [(MetricClock.SNAPSHOT_OBSERVED,0),(MetricClock.COMPLETION_MODEL,0),(MetricClock.COMPLETION_MODEL,24)]:
        mapped=source.asof_hours(hours,clock=mode,delay_hours=lag,max_age_hours=72)
        mapped.to_parquet(OUT/f'input_clock_{mode}_{lag}.parquet',index=False)
        diagnostics.append(dict(clock=str(mode),delay_hours=lag,status_counts=mapped.status.value_counts().to_dict(),
                                maximum_age_hours=float(mapped.age_hours.max()) if mapped.age_hours.notna().any() else None,
                                snapshot_observed_before_decision_hours=int(mapped.snapshot_observed_before_decision.sum())))
    conditional=[r for r in diagnostics if r['clock']=='completion_model']
    save(HERE/'input_admission.json',dict(snapshot_values_valid=True,strict_historical_pit_admitted=False,
        conditional_completion_models_complete=all(r['status_counts']=={'KNOWN':14592} for r in conditional),
        source_observed_at=str(observed),hourly_points=14592,daily_points=700,clocks=diagnostics,
        current_api=ref(HERE/'sources/community_api.json'),source_receipt=ref(HERE/'sources/community_api_receipt.json'),
        metrics=ref(OUT/'hash_daily.parquet'),owner_module=ref(owner),native_input_copy=ref(HERE/'OnchainInputs.py'),
        same_value_overlap=ref(HERE/'snapshot_overlap_audit.json'),futures_and_spot_links=links,
        caveat='Completion timestamps are calculation clocks, not historical publication or version certification. Only explicit offline conditional models may run.'))
    print(json.dumps(diagnostics),flush=True)


if __name__=='__main__':main()
