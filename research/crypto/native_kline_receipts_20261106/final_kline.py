"""Normalize explicit final K-line messages for the frozen causal feature evaluator."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sys

FEATURES = Path(__file__).resolve().parent.parent / 'rd_program_20261105/factors'
sys.path.insert(0, str(FEATURES))
from features import HourClose, decision_time

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
MS = 10**6
INTERVALS = {'5m': 300_000, '1h': 3_600_000, '4h': 14_400_000}


@dataclass(frozen=True)
class FinalKline:
    market: str
    symbol: str
    interval: str
    open_ms: int
    end_ms: int
    publish_ms: int
    received_ns: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    first_trade: int
    last_trade: int
    trade_count: int
    raw_sha256: str

    @property
    def values(self):
        return (self.open, self.high, self.low, self.close, self.volume,
                self.first_trade, self.last_trade, self.trade_count)


def parse_final(raw: str, received_ns: int, market: str) -> FinalKline | None:
    if market not in ('spot', 'futures') or type(received_ns) is not int or received_ns <= 0:
        raise ValueError('Explicit market and nanosecond receive time required')
    message = json.loads(raw)
    data = message['data']
    kline = data['k']
    symbol, interval = data['s'], kline['i']
    if (data['e'] != 'kline' or symbol != kline['s'] or not symbol.endswith('USDT')
            or interval not in INTERVALS or message['stream'] != f'{symbol.lower()}@kline_{interval}'
            or type(kline['x']) is not bool):
        raise ValueError('Unexpected K-line stream identity')
    opened, close_time, published = kline['t'], kline['T'], data['E']
    width = INTERVALS[interval]
    if (any(type(value) is not int for value in (opened, close_time, published))
            or opened <= 0 or opened % width or close_time != opened + width - 1
            or not opened <= published <= received_ns // MS):
        raise ValueError('Invalid exchange K-line time or receive chronology')
    if not kline['x']:
        return None
    if published < close_time or received_ns < (close_time + 1) * MS:
        raise ValueError('Final K-line received before the interval completed')
    o, h, low, c, volume = (Decimal(kline[key]) for key in ('o', 'h', 'l', 'c', 'v'))
    if (any(not value.is_finite() for value in (o, h, low, c, volume))
            or not 0 < low <= min(o, c) <= max(o, c) <= h or volume < 0):
        raise ValueError('Invalid final K-line OHLCV')
    first, last, count = kline['f'], kline['L'], kline['n']
    if (any(type(value) is not int for value in (first, last, count))
            or count < 0 or (count and not 0 <= first <= last)):
        raise ValueError('Invalid final K-line trade identities')
    return FinalKline(market, symbol, interval, opened, close_time + 1, published, received_ns,
                      o, h, low, c, volume, first, last, count, sha256(raw.encode()).hexdigest())


def datetime_ns(value: datetime) -> int:
    delta = value - EPOCH
    return ((delta.days * 86_400 + delta.seconds) * 10**6 + delta.microseconds) * 1000


def hour_closes(records, market, symbol, at):
    """Keep the exact first observable final receipt and preserve conflicting values."""
    decision = decision_time(at)
    cutoff = datetime_ns(decision)
    by_open = defaultdict(list)
    for row in records:
        if (row.market, row.symbol, row.interval) == (market, symbol, '1h') and row.end_ms * MS <= cutoff:
            by_open[row.open_ms].append(row)
    closes = []
    for opened, group in sorted(by_open.items()):
        rows = sorted(group, key=lambda row: (row.received_ns, row.raw_sha256))
        observed = [row for row in rows if row.received_ns <= cutoff]
        if not observed:
            # Keep a late receipt explicitly so the frozen evaluator reports the
            # correct blocker. Its price is never consumed at this decision.
            observed = rows[:1]
        values = set()
        for row in observed:
            if row.values in values:
                continue
            values.add(row.values)
            closes.append(HourClose(EPOCH + timedelta(milliseconds=opened), float(row.close),
                                    EPOCH + timedelta(microseconds=(row.received_ns + 999) // 1000)))
    return closes
