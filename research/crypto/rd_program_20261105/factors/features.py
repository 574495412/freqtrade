"""Fixed F1/F2 feature computation only: no strategy, backtest, network or orders.

The caller must supply observed availability timestamps. An OHLCV timestamp is
the opening time of an hour, not a receipt timestamp or permission to trade.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from statistics import stdev
from typing import Iterable, Mapping

HOUR = timedelta(hours=1)


@dataclass(frozen=True)
class HourClose:
    open_time: datetime
    close: float
    available_at: datetime | None


@dataclass(frozen=True)
class Feature:
    value: float | None
    blocked_reason: str | None
    decision_at: datetime
    proposed_execution_at: datetime
    observations: int


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware timestamp required")
    return value.astimezone(timezone.utc)


def decision_time(value: datetime) -> datetime:
    result = utc(value)
    if (result.hour, result.minute, result.second, result.microsecond) != (0, 0, 0, 0):
        raise ValueError("Frozen daily decision must be at UTC 00:00")
    return result


def complete_closes(rows: Iterable[HourClose], decision: datetime, count: int):
    """Return only the exact contiguous causal window, or an explicit blocker.

    Unknown/delayed receipt in the required window blocks the value. Never move
    the decision backward to use an older close, forward to wait for a late bar,
    or infer a receipt time. Future rows cannot affect a present value.
    """
    first, last = decision - count * HOUR, decision - HOUR
    indexed = {}
    for row in rows:
        opened = utc(row.open_time)
        if not first <= opened <= last:
            continue
        if (opened.minute, opened.second, opened.microsecond) != (0, 0, 0):
            return None, "off_hour_bar"
        if opened in indexed:
            return None, "duplicate_hour"
        indexed[opened] = row
    expected = [first + n * HOUR for n in range(count)]
    if any(t not in indexed for t in expected):
        return None, "missing_hour"
    values = []
    for opened in expected:
        row = indexed[opened]
        if row.available_at is None:
            return None, "availability_unknown"
        available = utc(row.available_at)
        if available < opened + HOUR:
            return None, "receipt_before_hour_complete"
        if available > decision:
            return None, "not_available_at_decision"
        if isinstance(row.close, bool) or not isinstance(row.close, (int, float)):
            return None, "invalid_close"
        if not math.isfinite(row.close) or row.close <= 0:
            return None, "invalid_close"
        values.append(float(row.close))
    return values, None


def momentum168(rows: Iterable[HourClose], at: datetime) -> Feature:
    decision = decision_time(at)
    closes, reason = complete_closes(rows, decision, 169)
    value = None if reason else math.log(closes[-1]) - math.log(closes[0])
    return Feature(value, reason, decision, decision + timedelta(minutes=5),
                   0 if reason else len(closes))


def volatility24_scale(rows: Iterable[HourClose], at: datetime) -> Feature:
    decision = decision_time(at)
    closes, reason = complete_closes(rows, decision, 25)
    scale = None
    if not reason:
        returns = [math.log(b) - math.log(a) for a, b in zip(closes, closes[1:])]
        sigma = stdev(returns) * math.sqrt(24 * 365)
        if sigma == 0:
            reason = "zero_volatility"
        elif not math.isfinite(sigma):
            reason = "nonfinite_volatility"
        else:
            scale = min(1.0, 0.20 / sigma)
    return Feature(scale, reason, decision, decision + timedelta(minutes=5),
                   0 if closes is None else len(closes))


def select_positive_top3(features: Mapping[str, Feature], universe: tuple[str, ...],
                         at: datetime) -> dict:
    """A selection intent, not executable targets or an order authorization.

    All frozen members must be evaluable; dropping a missing member would change
    the cross-section. Ties use the frozen universe order, not input order.
    """
    decision = decision_time(at)
    if not universe or len(universe) != len(set(universe)):
        raise ValueError("Nonempty unique frozen universe required")
    blocked = {}
    for pair in universe:
        feature = features.get(pair)
        if feature is None:
            blocked[pair] = "missing_member"
        elif feature.decision_at != decision:
            blocked[pair] = "different_decision_time"
        elif feature.proposed_execution_at != decision + timedelta(minutes=5):
            blocked[pair] = "different_execution_time"
        elif feature.blocked_reason:
            blocked[pair] = feature.blocked_reason
        elif feature.value is None or not math.isfinite(feature.value):
            blocked[pair] = "invalid_feature"
    selected = []
    if not blocked:
        candidates = [(features[pair].value, index, pair) for index, pair in enumerate(universe)
                      if features[pair].value > 0]
        candidates.sort(key=lambda item: (-item[0], item[1]))
        selected = [item[2] for item in candidates[:3]]
    return {"selected": selected, "blocked_members": blocked,
            "decision_at": decision.isoformat(),
            "proposed_execution_at": (decision + timedelta(minutes=5)).isoformat(),
            "orders_enabled": False, "RD1_applied": False, "economic_qualified": False}
