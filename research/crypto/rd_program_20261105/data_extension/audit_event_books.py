"""Replay the two registered public minute samples, retaining exact arrival order."""
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
import gzip,hashlib,json,sys
from book_replay import Book,nanoseconds

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3]
DATA=ROOT/'user_data/crypto_quant/rd_program_20261105_v1'
OUT=DATA/'event_book_replay_v001';REG=HERE/'event_book_replay_registration.json'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,obj):
    with p.open('x') as f:json.dump(obj,f,indent=2);f.write('\n')


def run_case(market,path):
    book=Book(market);previous=None;count=0;disconnects=0;observations=[];lags=[];digits=Counter();errors=[]
    body=gzip.decompress(path.read_bytes()).decode()
    for line_number,line in enumerate(body.splitlines(),1):
        if not line.strip():book.reset();disconnects+=1;continue
        local,raw=line.split(' ',1);at=nanoseconds(local);event=json.loads(raw);count+=1
        if previous is not None and at<previous:raise ValueError('exact local time regressed')
        previous=at;digits[len(local.split('.')[1][:-1]) if '.' in local else 0]+=1
        stream=event['stream'];data=event['data']
        # Jan2025 captured E is milliseconds for these two documented feed formats.
        # Funding T is next settlement time, never a publication-latency timestamp.
        if 'E' in data:lags.append(at-int(data['E'])*1000000)
        try:
            if stream=='btcusdt@depthSnapshot':book.snapshot(data)
            elif stream.startswith('btcusdt@depth@'):book.update(data)
            else:continue
            row=book.observation()
            if row is not None:
                observations.append(dict(row,received_at=local,received_ns=at,source_line=line_number))
        except ValueError as exc:
            errors.append({'source_line':line_number,'received_at':local,'error':str(exc)})
            break  # Keep failure; do not reconnect with an invented snapshot.
    curve=OUT/(market+'_observations.jsonl')
    with curve.open('x') as f:
        for row in observations:f.write(json.dumps(row)+'\n')
    return {'market':market,'input_path':str(path.relative_to(ROOT)),'input_sha256':sha(path),
            'input_messages_processed':count,'local_timestamp_fraction_digits':dict(digits),
            'exact_local_timestamp_order_passed':not errors and count==sum(bool(x.strip()) for x in body.splitlines()),
            'disconnects':disconnects,'applied_depth_updates_including_buffer':book.applied,'obsolete_depth_updates':book.obsolete,
            'errors':errors,'usable_book_observations':len(observations),
            'bbo_known_region_observations':sum(x['bbo_within_known_snapshot_region'] for x in observations),
            'complete_bid_20bps_observations':sum(x['bid_band_complete'] for x in observations),
            'complete_ask_20bps_observations':sum(x['ask_band_complete'] for x in observations),
            'complete_both_20bps_observations':sum(x['bid_band_complete'] and x['ask_band_complete'] for x in observations),
            'first_observation':observations[0] if observations else None,'last_observation':observations[-1] if observations else None,
            'arrival_minus_exchange_E_ns':{'minimum':min(lags) if lags else None,'maximum':max(lags) if lags else None,'negative_count':sum(x<0 for x in lags),'not_certified_network_latency':True},
            'output':{'path':str(curve.relative_to(ROOT)),'sha256':sha(curve)},'economic_qualified':False}


if __name__=='__main__':
    paths={m:DATA/'event_source_probe_v001'/f'{exchange}_2025-01-01.raw' for m,exchange in [('spot','binance'),('futures','binance-futures')]}
    sources=[Path(__file__),HERE/'book_replay.py',HERE/'test_book_replay.py']
    if sys.argv[1:]==['register']:
        write(REG,{'registered_at_utc':datetime.now(timezone.utc).isoformat(),
                   'input_hashes':{str(p.relative_to(ROOT)):sha(p) for p in [*paths.values(),*sources]},
                   'market_inputs':{m:str(p.relative_to(ROOT)) for m,p in paths.items()},
                   'sources':'Binance official spot U/u and futures U/u/pu rules; finite initial snapshot boundary retained',
                   'tests_passed':8,'new_financial_runs':0,'orders_enabled':False})
    else:
        reg=json.loads(REG.read_text())
        for p,digest in reg['input_hashes'].items():
            if sha(ROOT/p)!=digest:raise ValueError('input drift')
        OUT.mkdir(exist_ok=False)
        write(OUT/'start.json',{'started_at_utc':datetime.now(timezone.utc).isoformat(),'registration_sha256':sha(REG)})
        rows=[run_case(m,ROOT/p) for m,p in reg['market_inputs'].items()]
        for p,digest in reg['input_hashes'].items():
            if sha(ROOT/p)!=digest:raise ValueError('post-run input drift')
        write(OUT/'result.json',{'completed_at_utc':datetime.now(timezone.utc).isoformat(),'registration_sha256':sha(REG),'rows':rows,'financial_runs':0,'orders_enabled':False,'economic_qualified':False,'full_window_coverage':False})
        for r in rows:print(json.dumps({k:r[k] for k in ['market','usable_book_observations','complete_both_20bps_observations','errors','first_observation']}))
