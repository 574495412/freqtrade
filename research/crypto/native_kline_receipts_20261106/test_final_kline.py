"""Synthetic faults for receipt normalization and frozen feature causality."""
from datetime import datetime, timezone
import json

import pytest

from final_kline import MS, datetime_ns, hour_closes, parse_final
from features import momentum168, volatility24_scale

AT = datetime(2026, 8, 31, tzinfo=timezone.utc)
AT_NS = datetime_ns(AT)
HOUR_MS = 3_600_000


def frame(opened, close='100', *, symbol='BTCUSDT', final=True, published=None):
    end = opened + HOUR_MS
    return dict(stream=f'{symbol.lower()}@kline_1h', data=dict(e='kline', s=symbol,
        E=end if published is None else published,
        k=dict(t=opened,T=end-1,s=symbol,i='1h',x=final,o='100',h='200',l='90',
               c=close,v='1',f=100,L=100,n=1)))


def sample(end_ns, close='100', *, offset=0, market='spot', **kwargs):
    return parse_final(json.dumps(frame(end_ns//MS-HOUR_MS,close,**kwargs)), end_ns+offset, market)


def history(*, last_offset=0):
    step = HOUR_MS * MS
    return [sample(AT_NS-n*step, str(100+n%4+n/100), offset=last_offset if n==0 else 0)
            for n in reversed(range(169))]


def evaluate(records):
    rows = hour_closes(records,'spot','BTCUSDT',AT)
    return momentum168(rows,AT), volatility24_scale(rows,AT)


def test_public_final_uses_separate_publish_and_receipt_times():
    row = sample(AT_NS, offset=20_000_001, published=AT_NS//MS+5)
    assert row.end_ms == AT_NS//MS
    assert row.publish_ms == AT_NS//MS+5
    assert row.received_ns == AT_NS+20_000_001
    assert len(row.raw_sha256)==64


def test_provisional_or_missing_final_cannot_supply_a_close():
    assert sample(AT_NS,final=False) is None
    assert all(f.blocked_reason=='missing_hour' for f in evaluate(history()[:-1]))


def test_one_nanosecond_late_blocks_both_frozen_features():
    assert all(f.blocked_reason is None for f in evaluate(history()))
    assert all(f.blocked_reason=='not_available_at_decision' for f in evaluate(history(last_offset=1)))


def test_duplicate_is_idempotent_but_conflicting_observed_close_blocks():
    records = history()
    assert evaluate(records+[records[-1]])==evaluate(records)
    assert all(f.blocked_reason=='duplicate_hour' for f in evaluate(records+[sample(AT_NS,'190')]))


def test_later_conflict_and_other_market_do_not_change_past_result():
    records = history()
    late = sample(AT_NS,'190',offset=1)
    futures = sample(AT_NS,'190',market='futures')
    assert evaluate(records+[late,futures])==evaluate(records)


@pytest.mark.parametrize('mutate', [
    lambda frame: frame['data'].update(E=AT_NS//MS+1),
    lambda frame: frame['data']['k'].update(T=AT_NS//MS),
    lambda frame: frame['data']['k'].update(h='99'),
    lambda frame: frame['data']['k'].update(x='true'),
    lambda frame: frame.update(stream='solusdt@kline_1h'),
])
def test_inconsistent_frame_or_clock_is_rejected(mutate):
    msg = frame(AT_NS//MS-HOUR_MS)
    mutate(msg)
    with pytest.raises(ValueError):
        parse_final(json.dumps(msg),AT_NS,'spot')
