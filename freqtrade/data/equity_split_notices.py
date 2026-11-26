"""Local split-announcement revisions, not split execution or adjusted prices.

Source fields are explicitly interpreted input. Matching bytes and supplied
clocks do not authenticate an issuer, historical first-seen, or completeness.
Missing announcements never mean no split. No quantities/cash/orders change.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from urllib.parse import urlsplit

from freqtrade.data.history.equity import EquityAdmissionError


def _clock(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise EquityAdmissionError("Explicit timezone-aware notice clock required")
    try:
        offset = value.utcoffset()
    except (TypeError, ValueError, OverflowError) as error:
        raise EquityAdmissionError("Invalid notice clock") from error
    if offset is None:
        raise EquityAdmissionError("Explicit timezone-aware notice clock required")
    return value


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,100}", value):
        raise EquityAdmissionError("Explicit exact notice/security/revision identity required")


@dataclass(frozen=True)
class SplitSource:
    raw: bytes
    url: str
    sha256: str
    http_status: int

    def __post_init__(self):
        if type(self.raw) is not bytes or not self.raw or self.http_status != 200:
            raise EquityAdmissionError("Successful original source bytes required")
        if hashlib.sha256(self.raw).hexdigest() != self.sha256:
            raise EquityAdmissionError("Split source byte identity mismatch")
        if not isinstance(self.url, str):
            raise EquityAdmissionError("Original HTTPS source URL required")
        url = urlsplit(self.url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise EquityAdmissionError("Original HTTPS source URL required")


@dataclass(frozen=True)
class SplitNotice:
    event_id: str
    revision_id: str
    security_id: str
    pair: str
    calendar: str
    state: str
    published_at: datetime | None
    observed_at: datetime | None
    source: SplitSource
    old_shares: int | None = None
    new_shares: int | None = None
    planned_trade_open: datetime | None = None
    record_at: datetime | None = None
    distribution_at: datetime | None = None

    def __post_init__(self):
        for value in (self.event_id, self.revision_id, self.security_id):
            _identity(value)
        pattern = {"XNYS": r"[A-Z0-9][A-Z0-9.-]*/USD", "XHKG": r"[0-9]{5}/HKD"}
        if (
            not isinstance(self.calendar, str)
            or self.calendar not in pattern
            or not isinstance(self.pair, str)
            or not re.fullmatch(pattern[self.calendar], self.pair)
        ):
            raise EquityAdmissionError("Exact covered USD/HKD security and calendar required")
        if not isinstance(self.source, SplitSource):
            raise EquityAdmissionError("Bound original notice source required")
        if not isinstance(self.state, str) or self.state not in {
            "proposed",
            "approved",
            "cancelled",
        }:
            raise EquityAdmissionError("Notice state is not confirmation of an executed split")
        for value in (
            self.published_at,
            self.observed_at,
            self.planned_trade_open,
            self.record_at,
            self.distribution_at,
        ):
            if value is not None:
                _clock(value)
        if (
            self.published_at is not None
            and self.observed_at is not None
            and self.observed_at < self.published_at
        ):
            raise EquityAdmissionError("Observation cannot precede supplied publication")
        if self.state == "cancelled":
            if any(
                x is not None
                for x in (
                    self.old_shares,
                    self.new_shares,
                    self.planned_trade_open,
                    self.record_at,
                    self.distribution_at,
                )
            ):
                raise EquityAdmissionError("Cancelled notice cannot carry stale split terms")
        elif any(type(x) is not int or x <= 0 for x in (self.old_shares, self.new_shares)):
            raise EquityAdmissionError("Positive integer old/new share terms required")
        elif self.old_shares == self.new_shares:
            raise EquityAdmissionError("One-for-one is not an evidenced split announcement")
        # Record/distribution clocks never infer or replace the trading boundary.
        # Past confirmations may be received after a planned trading boundary.

    def visible(self, at):
        _clock(at)
        return (
            self.published_at is not None
            and self.observed_at is not None
            and max(self.published_at, self.observed_at) <= at
        )


@dataclass(frozen=True)
class SplitNoticeBook:
    notices: tuple[SplitNotice, ...]

    def __post_init__(self):
        if not isinstance(self.notices, tuple):
            raise EquityAdmissionError("Immutable split revision history required")
        revisions, publications, bindings = set(), set(), {}
        for notice in self.notices:
            if not isinstance(notice, SplitNotice):
                raise EquityAdmissionError("Typed split revision required")
            key = (notice.pair, notice.security_id, notice.event_id)
            binding = (notice.calendar, notice.security_id)
            if notice.pair in bindings and bindings[notice.pair] != binding:
                raise EquityAdmissionError("No implicit historical security/calendar remapping")
            bindings[notice.pair] = binding
            revision = (*key, notice.revision_id)
            publication = (*key, notice.published_at)
            if revision in revisions or (
                notice.published_at is not None and publication in publications
            ):
                raise EquityAdmissionError("Duplicate or ambiguous notice revision")
            revisions.add(revision)
            publications.add(publication)

    def lookup(self, pair, security_id, event_id, *, at):
        _clock(at)
        _identity(security_id)
        _identity(event_id)
        view = {
            "status": "unknown",
            "pair": pair,
            "security_id": security_id,
            "event_id": event_id,
            "revision_id": None,
            "notice_state": None,
            "old_shares": None,
            "new_shares": None,
            "price_factor": None,
            "quantity_factor": None,
            "direction": None,
            "planned_trade_open": None,
            "record_at": None,
            "distribution_at": None,
            "published_at": None,
            "observed_at": None,
            "source_sha256": None,
            "execution_confirmed": None,
        }
        visible = [
            n
            for n in self.notices
            if (
                n.pair == pair
                and n.security_id == security_id
                and n.event_id == event_id
                and n.visible(at)
            )
        ]
        if not visible:
            return view
        # Publication orders revisions; a late arrival of an older publication
        # must not supersede a visible later amendment/cancellation.
        chosen = max(visible, key=lambda n: n.published_at)
        view.update(
            status="known",
            revision_id=chosen.revision_id,
            notice_state=chosen.state,
            old_shares=chosen.old_shares,
            new_shares=chosen.new_shares,
            planned_trade_open=chosen.planned_trade_open,
            record_at=chosen.record_at,
            distribution_at=chosen.distribution_at,
            published_at=chosen.published_at,
            observed_at=chosen.observed_at,
            source_sha256=chosen.source.sha256,
        )
        if chosen.state != "cancelled":
            view.update(
                price_factor=Fraction(chosen.old_shares, chosen.new_shares),
                quantity_factor=Fraction(chosen.new_shares, chosen.old_shares),
                direction="forward" if chosen.new_shares > chosen.old_shares else "reverse",
            )
        return view
