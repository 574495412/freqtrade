"""Local native signal construction from source daily positioning observations."""
import hashlib,importlib.util,json
from pathlib import Path
import numpy as np
import pandas as pd

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def owner(study):
    path=study.parents[2]/'freqtrade/data/binance_positioning.py'
    spec=importlib.util.spec_from_file_location('native_positioning_input',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def signal_frames(study,output):
    module=owner(study);records=json.loads((output/'raw_receipts.json').read_text())
    fingerprint={'receipts_sha256':sha(output/'raw_receipts.json'),'input_owner_sha256':sha(Path(module.__file__)),
                 'policy_sha256':sha(Path(__file__)),'protocol_sha256':sha(study/'protocol.json')}
    capsule=output/'signal_capsule.json'
    if capsule.exists():
        saved=json.loads(capsule.read_text())
        if saved['identity']!=fingerprint:raise ValueError('Positioning input or policy cache drift')
        for name,r in saved['files'].items():
            if sha(r['path'])!=r['sha256']:raise ValueError('Positioning cached feature drift')
        return {name:pd.read_parquet(r['path'])for name,r in saved['daily_files'].items()},saved
    allframes={};files={};diagnostics={};unknown={}
    for symbol in ['BTCUSDT','ETHUSDT','SOLUSDT']:
        daily,diag=module.daily_positioning(records,symbol)
        allframes[symbol]=daily
        path=output/f'daily_{symbol}.parquet';daily.to_parquet(path);files[symbol]={'path':str(path),'sha256':sha(path)}
        diagnostics[symbol]=diag
        unknown[symbol]=daily.isna().sum().to_dict()
    saved={'identity':fingerprint,'files':files,'daily_files':files,'diagnostics':diagnostics,'unknown_daily_fields':unknown,
           'source_event_return_or_financial_engine_executed':False}
    capsule.write_text(json.dumps(saved,indent=2)+'\n');return allframes,saved

def opportunities(study,daily,mode):
    module=owner(study);column={'crowd':'count_long_short_ratio','position':'sum_toptrader_long_short_ratio'}[mode]
    rank=module.trailing_midrank(daily[column])
    return module.monthly_opportunities(rank,'2025-01-01','2026-08-31')
