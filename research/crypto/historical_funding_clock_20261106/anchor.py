"""Probe a continuous prefix for real snapshots before the sampled funding receipts."""
from collections import Counter
import json
from pathlib import Path
import sys

from capture import DAY, HERE, OUT, ROOT, SYMBOLS, capture, now, sample, sha, verify, write
from audit import messages, RECEIPTS
from book_replay import Book, nanoseconds
from probe_event_sources import decode

PLAN = HERE/'anchor_registration.json'
AUDIT = HERE/'audit_result.json'


def register():
    item = sample(DAY,0,['depth','depthSnapshot'])
    item.update(id='funding-predecessor-0000-0005',url=item['url']+'&sliceSize=6',slice_minutes=6)
    write(PLAN, dict(registered_at_utc=now(), request=item, maximum_requests=1, retries=0,
        input_hashes={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in (Path(__file__),AUDIT,RECEIPTS)},
        scope='Search for a captured snapshot before the observed funding poll, preserving all unbridged updates',
        economic_admission=False))


def replay(receipt):
    funding = json.loads(AUDIT.read_text())['funding']
    cutoffs = {s:min(f['first_sampled_receive_ns'] for f in funding if f['symbol']==s) for s in SYMBOLS}
    books = {s:Book('futures') for s in SYMBOLS}
    summaries = {s:dict(snapshots=0,valid_book_observations=0,complete_20bps_observations=0,
                        complete_20bps_before_funding_poll=0,errors=[]) for s in SYMBOLS}
    start = nanoseconds(DAY+'T00:00:00Z')
    previous = start
    counts = Counter()
    overlapping = []
    raw = decode((ROOT/receipt['path']).read_bytes(),receipt['headers'])
    for line_number,line in enumerate(raw.decode().splitlines(),1):
        if not line.strip():
            counts['disconnect'] += 1
            for book in books.values():
                book.reset()
            continue
        local,text = line.split(' ',1)
        at = nanoseconds(local)
        if not previous<=at<start+6*60*10**9:
            raise ValueError('Prefix chronology or slice coverage differs from registration')
        previous = at
        message = json.loads(text)
        symbol,channel = message['stream'].split('@',1)
        symbol = symbol.upper()
        if channel!='depthSnapshot' and not channel.startswith('depth@'):
            raise ValueError('Unexpected channel in predecessor slice')
        counts[message['stream']] += 1
        if at>=start+5*60*10**9:
            overlapping.append((local,message))
        summary,book = summaries[symbol],books[symbol]
        try:
            if channel=='depthSnapshot':
                summary['snapshots'] += 1
                summary.setdefault('first_snapshot_received_at',local)
                book.snapshot(message['data'])
            elif not book.failed:
                book.update(message['data'])
            observation = book.observation()
            if observation is not None:
                summary['valid_book_observations'] += 1
                full = observation['bid_band_complete'] and observation['ask_band_complete']
                summary['complete_20bps_observations'] += int(full)
                if at<=cutoffs[symbol]:
                    summary['latest_before_funding_poll'] = dict(observation,collector_received_at=local,source_line=line_number)
                    summary['complete_20bps_before_funding_poll'] += int(full)
        except ValueError as exc:
            summary['errors'].append(dict(source_line=line_number,error=str(exc)))
            book.failed = True
    original = next(r for r in json.loads(RECEIPTS.read_text())['receipts'] if 'depth' in r['channels'])
    expected = [(local,message) for _,local,_,message in messages(original) if message is not None
                and (message['stream'].split('@',1)[1]=='depthSnapshot' or '@depth@' in message['stream'])]
    for s,summary in summaries.items():
        summary['unbridged_updates_at_end'] = len(books[s].buffer)
    return dict(stream_counts=dict(counts),symbols=summaries,overlapping_messages=len(overlapping),
                overlap_exactly_matches_prior_capture=overlapping==expected,prior_capture_overlap_rows=len(expected))


def run():
    plan = json.loads(PLAN.read_text())
    verify(plan)
    receipt = capture(plan['request'])
    result = dict(registration_sha256=sha(PLAN.read_bytes()),receipt=receipt,financial_accounts=0,economic_admission=False)
    if receipt.get('http_status')==200 and receipt['response_state']=='body_complete':
        try:
            result['replay'] = replay(receipt)
        except ValueError as exc:
            result['replay_error'] = str(exc)
    verify(plan)
    write(HERE/'anchor_result.json',result)


if __name__ == '__main__':
    if sys.argv[1:] == ['register']:
        register()
    elif sys.argv[1:] == ['run']:
        run()
    else:
        raise SystemExit('Use register or run')
