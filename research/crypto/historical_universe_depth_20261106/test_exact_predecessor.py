"""Synthetic exact-sequence faults for the targeted predecessor replay."""
from unittest.mock import patch

import pytest

from exact_predecessor import recover

MS = 10**6
BASE = 1793491500000
PAIR = 'BTC/USDT:USDT'
SYMBOL = 'btcusdt'
TARGET = dict(pair=PAIR, cutoff_ns=BASE*MS, expected_snapshot_id=100,
              expected_snapshot_line=2, expected_first_update_id=105)
DEPTH = dict(market='futures',day='2026-11-01')


def update(line, prev, first, last, received_ms):
    return line, received_ms*MS, (SYMBOL,'depth@0ms',dict(data=dict(
        s='BTCUSDT',E=received_ms-2,T=received_ms-3,U=first,u=last,pu=prev,b=[],a=[])))


def snapshot(*, received_ms=BASE-100, narrow=False):
    return 2, received_ms*MS, (SYMBOL,'depthSnapshot',dict(generated=True, data=dict(
        lastUpdateId=100,E=BASE-250,T=BASE-251,
        bids=[['100','1'],['99.9' if narrow else '99','1']],
        asks=[['100.1','1'],['100.2' if narrow else '101','1']])))


def replay(frames):
    with patch('exact_predecessor.timeline', return_value=iter(frames)):
        with patch('exact_predecessor.inspect_bbo', return_value=({SYMBOL:{}}, {})):
            return recover(TARGET,DEPTH,{})


def test_buffered_update_exactly_follows_received_snapshot():
    first=update(1,100,103,105,BASE-200)
    result=replay([first,snapshot(),update(3,105,107,109,BASE-10)])
    assert result['applied_updates']==2
    assert result['first_bridge_update']['received_ns'] < result['snapshot']['received_ns']
    assert result['known_at_ns']==(BASE-10)*MS
    assert result['both_sides_20bps_complete']


def test_snapshot_arrival_controls_cutoff_availability():
    first=update(1,100,103,105,BASE-200)
    result=replay([first,snapshot()])
    assert result['known_at_ns']==(BASE-100)*MS
    with pytest.raises(ValueError,match='No complete observed'):
        replay([first,snapshot(received_ms=BASE+1)])


@pytest.mark.parametrize('broken', [
    [update(1,99,103,105,BASE-200),snapshot()],
    [update(1,100,103,105,BASE-200),snapshot(),update(3,104,107,109,BASE-10)],
])
def test_discontinuous_pu_fails_at_bootstrap_or_later(broken):
    with pytest.raises(ValueError,match='directly follow|sequence gap'):
        replay(broken)


def test_continuity_does_not_widen_original_depth():
    result=replay([update(1,100,103,105,BASE-200),snapshot(narrow=True)])
    assert result['fresh_known_bbo']
    assert not result['both_sides_20bps_complete']
