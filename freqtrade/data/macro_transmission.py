"""Local daily macro quote contract, not an upstream factor or a trading policy.

Report labels are not release clocks. The default uses actual snapshot receipt;
the explicit next-day06UTC scenario is a retrospective assumption only.
"""

import json
import math
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from numbers import Real
from zoneinfo import ZoneInfo


class MacroInputError(ValueError):
    pass


class MacroClock(StrEnum):
    SNAPSHOT_OBSERVED = "snapshot_observed"
    NEXT_DAY_06UTC_MODEL = "next_day_06utc_model"


def _aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MacroInputError("Timezone-aware macro observation clock required")
    return value.astimezone(UTC)


def _available(day):
    return datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC) + timedelta(hours=6)


@dataclass(frozen=True)
class MacroQuote:
    session_day: date
    provider_bar_label: datetime
    close: float | None


@dataclass(frozen=True)
class MacroQuoteSnapshot:
    symbol: str
    timezone: str
    observed_at: datetime
    quotes: tuple[MacroQuote, ...]
    _ready: tuple[datetime, ...] = field(init=False, repr=False)
    _valid: tuple[int, ...] = field(init=False, repr=False)
    _nulls: tuple[int, ...] = field(init=False, repr=False)

    def __post_init__(self):
        if self.symbol not in {"^TNX", "DX-Y.NYB"}:
            raise MacroInputError("Exact source index identity required")
        expected_zone = "America/Chicago" if self.symbol == "^TNX" else "America/New_York"
        if self.timezone != expected_zone:
            raise MacroInputError("Unexpected source index timezone")
        _aware(self.observed_at)
        if type(self.quotes) is not tuple or not self.quotes:
            raise MacroInputError("Immutable nonempty quote history required")
        previous = None
        for row in self.quotes:
            if not isinstance(row, MacroQuote) or type(row.session_day) is not date:
                raise MacroInputError("Explicit source session date required")
            stamp = _aware(row.provider_bar_label)
            if stamp > self.observed_at:
                raise MacroInputError("Source bar label cannot postdate actual snapshot receipt")
            if stamp.astimezone(ZoneInfo(self.timezone)).date() != row.session_day:
                raise MacroInputError("Source timestamp and local session date conflict")
            if previous is not None and row.session_day <= previous:
                raise MacroInputError("Duplicate/unordered macro session dates")
            if row.close is not None and (
                isinstance(row.close, bool)
                or not isinstance(row.close, Real)
                or not math.isfinite(row.close)
                or (self.symbol == "DX-Y.NYB" and row.close <= 0)
            ):
                raise MacroInputError("Invalid declared macro quote units")
            previous = row.session_day
        object.__setattr__(
            self, "_ready", tuple(_available(row.session_day) for row in self.quotes)
        )
        object.__setattr__(
            self, "_valid", tuple(i for i, row in enumerate(self.quotes) if row.close is not None)
        )
        counts = [0]
        for row in self.quotes:
            counts.append(counts[-1] + int(row.close is None))
        object.__setattr__(self, "_nulls", tuple(counts))

    def level_at(self, at, *, clock=MacroClock.SNAPSHOT_OBSERVED, max_age_hours=120, known_at=None):
        """Latest published-quote scenario; missing raw rows never become new values.

        A carried quote remains explicitly dated. This does not certify why a
        newer raw row is missing, a venue holiday, or a current session's close.
        """
        at = _aware(at)
        mode = MacroClock(clock)
        knowledge = at if known_at is None else _aware(known_at)
        if knowledge < at:
            raise MacroInputError("Knowledge clock cannot precede requested endpoint")
        if (
            isinstance(max_age_hours, bool)
            or not isinstance(max_age_hours, int)
            or max_age_hours <= 0
        ):
            raise MacroInputError("Positive explicit whole-hour staleness limit required")
        empty = {"status": "UNKNOWN", "value": None, "session_day": None, "age_hours": None}
        if mode == MacroClock.SNAPSHOT_OBSERVED and knowledge < self.observed_at:
            return dict(empty, reason="snapshot_not_yet_observed")
        eligible_index = bisect_right(self._ready, at) - 1
        valid_index = bisect_right(self._valid, eligible_index) - 1
        if valid_index < 0:
            return dict(empty, reason="no_completed_observed_quote")
        row_index = self._valid[valid_index]
        row = self.quotes[row_index]
        age = (at - self._ready[row_index]).total_seconds() / 3600
        if age > max_age_hours:
            return dict(empty, reason="stale_quote", age_hours=age)
        return {
            "status": "KNOWN_SNAPSHOT" if mode == MacroClock.SNAPSHOT_OBSERVED else "MODEL_ONLY",
            "value": float(row.close),
            "session_day": row.session_day.isoformat(),
            "available_at_model": _available(row.session_day).isoformat(),
            "age_hours": age,
            "later_raw_null_rows": self._nulls[eligible_index + 1] - self._nulls[row_index + 1],
            "historical_first_seen_certified": False,
            "knowledge_at": knowledge.isoformat(),
            "reason": None,
        }


def parse_yahoo_macro(body, *, symbol, observed_at):
    """Exact raw quote stream; null closes retained, never fed through adjclose."""
    try:
        payload = json.loads(body)
        chart = payload["chart"]
        if chart.get("error") is not None or len(chart["result"]) != 1:
            raise MacroInputError("Yahoo error response is not macro data")
        result = chart["result"][0]
        meta = result["meta"]
        if (
            meta["symbol"] != symbol
            or meta["instrumentType"] != "INDEX"
            or meta["dataGranularity"] != "1d"
        ):
            raise MacroInputError("Unexpected source index, instrument or frequency")
        zone = ZoneInfo(meta["exchangeTimezoneName"])
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        if len(timestamps) != len(closes):
            raise MacroInputError("Quote timestamp/value cardinality differs")
        rows = []
        for stamp, value in zip(timestamps, closes, strict=True):
            if type(stamp) is not int or not 0 < stamp < 10**11:
                raise MacroInputError("Integer epoch-second source bar labels required")
            label = datetime.fromtimestamp(stamp, UTC)
            rows.append(MacroQuote(label.astimezone(zone).date(), label, value))
        return MacroQuoteSnapshot(symbol, str(zone), observed_at, tuple(rows))
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        if isinstance(error, MacroInputError):
            raise
        raise MacroInputError("Malformed original macro response") from error


def macro_headwind_at(yield_quotes, dollar_quotes, *, at, clock=MacroClock.SNAPSHOT_OBSERVED):
    """Fixed source10-calendar-day AND gate, not a profitability result.

    No caller-selectable lookback, sign or reduced weight. Querying at-10days
    selects the prior dated endpoint; both endpoints must be observed by the
    current feature clock. A snapshot acquired today can describe prior quotes
    today, without certifying their historical first-seen time.
    """
    at = _aware(at)
    if yield_quotes.symbol != "^TNX" or dollar_quotes.symbol != "DX-Y.NYB":
        raise MacroInputError("Yield and dollar identities cannot be swapped")
    values = [
        source.level_at(stamp, clock=clock, known_at=at)
        for source in (yield_quotes, dollar_quotes)
        for stamp in (at, at - timedelta(days=10))
    ]
    result = {"status": "UNKNOWN", "headwind": None, "target_fraction": None}
    if any(value["value"] is None for value in values):
        return dict(result, reason="one_or_more_endpoints_unavailable")
    rate_now, rate_prior, dollar_now, dollar_prior = [row["value"] for row in values]
    rate_change = rate_now - rate_prior
    dollar_log_change = math.log(dollar_now) - math.log(dollar_prior)
    flag = rate_change > 0 and dollar_log_change > 0
    return dict(
        result,
        status="KNOWN_SNAPSHOT"
        if MacroClock(clock) == MacroClock.SNAPSHOT_OBSERVED
        else "MODEL_ONLY",
        headwind=flag,
        target_fraction=0.25 if flag else 1.0,
        reason=None,
        yield_change_percentage_points=rate_change,
        dollar_log_change=dollar_log_change,
        endpoints=values,
        historical_first_seen_certified=False,
    )
