"""Local SFC weekly reported-inventory input, distinct from short-sale flow.

Parsing and declared clocks do not authenticate provenance, dissemination, the
complete market inventory or a profitable factor. No missing-position zero fill.
"""

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from re import fullmatch
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from freqtrade.data.history.equity import EquityAdmissionError


HK = ZoneInfo("Asia/Hong_Kong")
HEADER = (
    "Date",
    "Stock Code",
    "Stock Name",
    "Aggregated Reportable Short Positions (Shares)",
    "Aggregated Reportable Short Positions (HK$)",
)


def _clock(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise EquityAdmissionError("Timezone-aware short-position clock required")
    return value


def _number(text, *, integer=False):
    if text == "n.a." and not integer:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise EquityAdmissionError("Invalid SFC position units") from error
    if not value.is_finite() or value < 0 or (integer and value != value.to_integral_value()):
        raise EquityAdmissionError("Nonnegative finite SFC shares/value required")
    return int(value) if integer else value


@dataclass(frozen=True)
class ReportedShortPosition:
    code: str
    name: str
    shares: int
    hkd_value: Decimal | None


@dataclass(frozen=True)
class SFCShortPositionReport:
    reporting_date: date
    source_url: str
    source_sha256: str
    published_at: datetime | None
    observed_at: datetime | None
    positions: tuple[ReportedShortPosition, ...]

    def __post_init__(self):
        if type(self.reporting_date) is not date:
            raise EquityAdmissionError("Exact report date required")
        url = urlparse(self.source_url)
        if url.scheme != "https" or url.hostname not in {"sfc.hk", "www.sfc.hk"}:
            raise EquityAdmissionError("Original regulator source URL required")
        if not fullmatch("[0-9a-f]{64}", self.source_sha256):
            raise EquityAdmissionError("Exact source-byte hash required")
        for stamp in (self.published_at, self.observed_at):
            if stamp is not None and _clock(stamp).astimezone(HK).date() <= self.reporting_date:
                raise EquityAdmissionError("Report date is not a contemporaneous available date")
        if self.published_at and self.observed_at and self.observed_at < self.published_at:
            raise EquityAdmissionError("Observation predates publication")
        if type(self.positions) is not tuple or not self.positions:
            raise EquityAdmissionError("Immutable nonempty report rows required")
        codes = set()
        for row in self.positions:
            if (
                not isinstance(row, ReportedShortPosition)
                or not fullmatch(r"[0-9]{5}", row.code)
                or not 0 < int(row.code) <= 99999
                or not row.name.strip()
                or type(row.shares) is not int
                or row.shares < 0
                or row.code in codes
                or (
                    row.hkd_value is not None
                    and (
                        not isinstance(row.hkd_value, Decimal)
                        or not row.hkd_value.is_finite()
                        or row.hkd_value < 0
                    )
                )
            ):
                raise EquityAdmissionError("Invalid/duplicate source counter or position units")
            codes.add(row.code)


def parse_sfc_csv(body, *, reporting_date, source_url, source_sha256, published_at, observed_at):
    """Strict source schema; caller supplies evidenced availability, never report+7d."""
    if not isinstance(body, bytes) or hashlib.sha256(body).hexdigest() != source_sha256:
        raise EquityAdmissionError("Source-byte hash mismatch")
    try:
        reader = csv.reader(io.StringIO(body.decode("utf-8-sig")))
        if tuple(next(reader)) != HEADER:
            raise EquityAdmissionError("Unexpected SFC CSV header; HTML is not report data")
        positions = []
        for row in reader:
            if len(row) != 5:
                raise EquityAdmissionError("Incomplete SFC CSV row")
            if datetime.strptime(row[0], "%d/%m/%Y").date() != reporting_date:
                raise EquityAdmissionError("Mixed or mismatched report dates")
            if not fullmatch(r"[0-9]{1,5}", row[1]):
                raise EquityAdmissionError("Explicit original stock counter required")
            positions.append(
                ReportedShortPosition(
                    row[1].zfill(5), row[2], _number(row[3], integer=True), _number(row[4])
                )
            )
    except (UnicodeError, StopIteration, ValueError, csv.Error) as error:
        raise EquityAdmissionError("Malformed original SFC CSV") from error
    return SFCShortPositionReport(
        reporting_date, source_url, source_sha256, published_at, observed_at, tuple(positions)
    )


def reported_position(report, code, *, at):
    """Explicit report lookup; output never normalizes by absent shares outstanding."""
    _clock(at)
    if not fullmatch(r"[0-9]{5}", code):
        raise EquityAdmissionError("Exact five-digit counter identity required")
    unknown = {"status": "unknown", "reported_shares": None, "reported_hkd_value": None}
    if (
        report is None
        or report.published_at is None
        or report.observed_at is None
        or max(report.published_at, report.observed_at) > at
    ):
        return dict(unknown, reason="report_not_observed_with_publication_clock")
    row = next((row for row in report.positions if row.code == code), None)
    if row is None:
        return dict(unknown, reason="counter_not_listed_not_zero")
    return {
        "status": "known_reported_inventory",
        "reported_shares": row.shares,
        "reported_hkd_value": row.hkd_value,
        "reason": None,
    }
