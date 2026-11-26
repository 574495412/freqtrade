"""Local research input boundary for original actual EPS and pre-release consensus.

Clock and identity checks cannot authenticate supplied provenance or completeness.
This is neither a new factor nor historical stock-account admission. Current
Yahoo consensus and SEC time-series EPS are not substitutes for this contract.
"""

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from numbers import Real
from re import fullmatch
from zoneinfo import ZoneInfo

from freqtrade.data.history.equity import EquityAdmissionError


def _aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise EquityAdmissionError("Timezone-aware earnings clock required")
    return value


@dataclass(frozen=True)
class EarningsValue:
    """A supplied source observation; unknown fields stay unknown."""

    event_id: str
    pair: str
    market: str
    currency: str
    fiscal_end: date
    basis: str  # Explicit GAAP/non-GAAP, basic/diluted and share-class identity.
    role: str  # original_actual or analyst_consensus
    value: float | None
    published_at: datetime | None
    observed_at: datetime | None
    source_sha256: str | None
    document_id: str | None

    def __post_init__(self):
        if (
            not self.event_id
            or not self.pair
            or not self.basis
            or self.role not in {"original_actual", "analyst_consensus"}
            or (self.market, self.currency) not in {("XNYS", "USD"), ("XHKG", "HKD")}
            or not self.pair.endswith("/" + self.currency)
            or type(self.fiscal_end) is not date
        ):
            raise EquityAdmissionError("Explicit earnings identity, role and units required")
        for clock in (self.published_at, self.observed_at):
            if clock is not None:
                _aware(clock)
        if self.published_at and self.observed_at and self.observed_at < self.published_at:
            raise EquityAdmissionError("Observation cannot predate supplied publication")
        if self.source_sha256 is not None and not fullmatch("[0-9a-f]{64}", self.source_sha256):
            raise EquityAdmissionError("Exact source-byte SHA256 required")


def consensus_surprise(actual: EarningsValue, forecast: EarningsValue, *, at: datetime):
    """Return a status without discarding an unknown event or imputing a zero.

    The percent kernel follows the reviewed ESA source: divide by absolute
    estimate, reject denominators below 0.01 and clip at +/-200 percent. Its
    availability rules here are a separate, stricter local input extension.
    """
    _aware(at)
    identity = ("event_id", "pair", "market", "currency", "fiscal_end", "basis")
    if any(getattr(actual, key) != getattr(forecast, key) for key in identity):
        raise EquityAdmissionError("Actual and consensus must describe the same EPS identity")
    if actual.role != "original_actual" or forecast.role != "analyst_consensus":
        raise EquityAdmissionError("Original EPS and analyst consensus roles required")
    result = {
        "event_id": actual.event_id,
        "pair": actual.pair,
        "status": "unknown",
        "percent_surprise": None,
        "reason": None,
    }
    for record in (actual, forecast):
        if (
            any(
                getattr(record, key) is None
                for key in ("value", "published_at", "observed_at", "source_sha256", "document_id")
            )
            or not record.document_id
        ):
            return dict(result, reason="missing_original_value_clock_or_source")
    if forecast.observed_at >= actual.published_at:
        return dict(result, reason="consensus_not_observed_strictly_before_release")
    if actual.observed_at > at or forecast.observed_at > at:
        return dict(result, reason="not_yet_observed")
    zone = ZoneInfo("America/New_York" if actual.market == "XNYS" else "Asia/Hong_Kong")
    if actual.fiscal_end >= actual.published_at.astimezone(zone).date():
        return dict(result, reason="fiscal_period_not_complete_before_release")
    if not all(
        isinstance(value, Real) and not isinstance(value, bool) and isfinite(value)
        for value in (actual.value, forecast.value)
    ):
        return dict(result, reason="nonfinite_eps")
    if abs(forecast.value) < 0.01:
        return dict(result, reason="near_zero_estimate")
    percent = (actual.value - forecast.value) / abs(forecast.value) * 100
    return dict(
        result, status="known", reason=None, percent_surprise=max(-200.0, min(200.0, percent))
    )
