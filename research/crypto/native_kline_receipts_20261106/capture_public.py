"""Capture one public K-line boundary in an isolated, uncredentialed sample."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time

import websockets

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'user_data/crypto_quant/rd_program_20261105_v1/public_kline_receipt_v001'
PLAN = HERE / 'capture_registration.json'
RESULT = HERE / 'capture_receipts.json'
STREAMS = 'btcusdt@kline_5m/btcusdt@kline_1h/solusdt@kline_5m/solusdt@kline_1h'
URLS = {'spot': 'wss://stream.binance.com:443/stream?streams=' + STREAMS,
        'futures': 'wss://fstream.binance.com/market/stream?streams=' + STREAMS}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    with path.open('x') as handle:
        json.dump(data, handle, indent=2)
        handle.write('\n')


def register():
    now = time.time_ns()
    target = (now // (300 * 10**9) + 1) * (300 * 10**9)
    if target - now < 12 * 10**9:
        target += 300 * 10**9
    write(PLAN, dict(registered_at_ns=now, target_boundary_ns=target,
                     stop_ns=target+8*10**9, markets=URLS, websocket_version=websockets.__version__,
                     maximum_frames_per_market=4096, maximum_frame_bytes=16384,
                     connect_timeout_seconds=10, retries=0, proxy=False,
                     evidence_class='prospective_public_collector_only', financial_accounts=0,
                     source_sha256=digest(Path(__file__))))


async def capture(market, url, plan):
    result = dict(market=market, url=url, started_ns=time.time_ns(), received_frames=0)
    path = OUT / (market + '.jsonl')
    try:
        async with websockets.connect(url, proxy=None, open_timeout=plan['connect_timeout_seconds'],
                                      max_size=plan['maximum_frame_bytes'], close_timeout=2,
                                      ping_interval=None) as ws:
            result['connected_ns'] = time.time_ns()
            with path.open('x') as handle:
                while result['received_frames'] < plan['maximum_frames_per_market']:
                    remaining = (plan['stop_ns'] - time.time_ns()) / 10**9
                    if remaining <= 0:
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except TimeoutError:
                        break
                    received = time.time_ns()
                    handle.write(json.dumps(dict(collector_received_ns=received, raw=raw)) + '\n')
                    result['received_frames'] += 1
            result['status'] = 'capture_finished'
    except Exception as exc:
        result.update(status='capture_error', error_type=type(exc).__name__,
                      http_status=getattr(getattr(exc, 'response', None), 'status_code', None))
    result['finished_ns'] = time.time_ns()
    if path.exists():
        result.update(path=str(path.relative_to(ROOT)), sha256=digest(path))
    return result


async def run():
    plan = json.loads(PLAN.read_text())
    if plan['source_sha256'] != digest(Path(__file__)) or plan['websocket_version'] != websockets.__version__:
        raise ValueError('Capture source changed')
    if time.time_ns() >= plan['target_boundary_ns']:
        raise ValueError('Registered collection boundary already passed')
    OUT.mkdir(parents=True, exist_ok=False)
    rows = await asyncio.gather(*(capture(market,url,plan) for market,url in plan['markets'].items()))
    write(RESULT, dict(registration_sha256=digest(PLAN), captures=rows, financial_accounts=0))


if __name__ == '__main__':
    if sys.argv[1:] == ['register']:
        register()
    elif sys.argv[1:] == ['run']:
        asyncio.run(run())
    else:
        raise SystemExit('Use register or run')
