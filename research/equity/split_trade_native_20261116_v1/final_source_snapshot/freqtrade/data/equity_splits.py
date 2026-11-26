"""Explicit implemented share-unit events; announcements alone are insufficient.

No prices, orders or cash are synthesized here. Historical event completeness,
implementation evidence and original availability remain caller-owned inputs.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction

from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.equity_wallets import EquityCashContract


def _aware(at):
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise EquityAdmissionError("Timezone-aware split clock required")
    return at


def _sha(value):
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise EquityAdmissionError("Original implemented split evidence SHA256 required")


@dataclass(frozen=True)
class ImplementedShareSplit:
    identity: str
    pair: str
    effective_open: datetime
    known_at: datetime
    new_shares: int
    old_shares: int
    source_sha256: str

    def __post_init__(self):
        if (
            not isinstance(self.identity, str)
            or not self.identity
            or not isinstance(self.pair, str)
        ):
            raise EquityAdmissionError("Exact split and ordinary-share identity required")
        if _aware(self.known_at) > _aware(self.effective_open):
            raise EquityAdmissionError("Late/unknown implementation clock cannot be backfilled")
        if any(type(n) is not int or n <= 0 for n in (self.new_shares, self.old_shares)):
            raise EquityAdmissionError("Exact positive integer new/old share terms required")
        if self.new_shares == self.old_shares:
            raise EquityAdmissionError("An identity ratio is not an implemented share split")
        _sha(self.source_sha256)

    @property
    def quantity_ratio(self):
        return Fraction(self.new_shares, self.old_shares)


@dataclass(frozen=True)
class ShareSplitContract:
    cash: EquityCashContract
    pairs: tuple[str, ...]
    coverage_start: datetime
    coverage_end: datetime
    events: tuple[ImplementedShareSplit, ...]
    source_sha256: str

    def __post_init__(self):
        if not isinstance(self.cash, EquityCashContract):
            raise EquityAdmissionError("Exact native equity cash contract required")
        if (
            type(self.pairs) is not tuple
            or not self.pairs
            or len(set(self.pairs)) != len(self.pairs)
        ):
            raise EquityAdmissionError("Immutable unique split security coverage required")
        if any(
            not isinstance(p, str) or not p.endswith("/" + self.cash.currency) for p in self.pairs
        ):
            raise EquityAdmissionError("Split shares must use the account currency")
        if _aware(self.coverage_start) >= _aware(self.coverage_end):
            raise EquityAdmissionError("Ordered complete split coverage clocks required")
        if type(self.events) is not tuple or any(
            not isinstance(e, ImplementedShareSplit) for e in self.events
        ):
            raise EquityAdmissionError("Immutable explicit implemented split rows required")
        identities, boundaries = set(), set()
        opens = {s.opens for s in self.cash.sessions}
        for event in self.events:
            key = (event.pair, event.effective_open)
            if event.identity in identities or key in boundaries:
                raise EquityAdmissionError("Duplicate/ambiguous implemented split event")
            identities.add(event.identity)
            boundaries.add(key)
            if event.pair not in self.pairs or event.effective_open not in opens:
                raise EquityAdmissionError(
                    "Split must bind an exact declared security/session open"
                )
            self.require_clock(event.effective_open)
        if tuple(sorted(self.events, key=lambda e: (e.effective_open, e.pair))) != self.events:
            raise EquityAdmissionError("Implemented split rows must be ordered")
        _sha(self.source_sha256)

    def require_clock(self, at):
        if not self.coverage_start <= _aware(at) <= self.coverage_end:
            raise EquityAdmissionError("Split clock outside declared complete coverage")

    def ratio(self, pair, original_at, asof):
        self.require_clock(original_at)
        self.require_clock(asof)
        if pair not in self.pairs or original_at > asof:
            raise EquityAdmissionError("Unknown split security or reversed unit clocks")
        value = Fraction(1)
        for event in self.events:
            if event.pair == pair and original_at < event.effective_open <= asof:
                value *= event.quantity_ratio
        return value

    def quantity(self, pair, quantity, original_at, asof):
        value = Fraction(Decimal(str(quantity))) * self.ratio(pair, original_at, asof)
        return Decimal(value.numerator) / Decimal(value.denominator)
