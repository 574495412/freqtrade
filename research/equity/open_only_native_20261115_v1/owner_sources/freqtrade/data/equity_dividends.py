"""Local ordinary same-currency cash dividend contract, not a custody feed.

The supplied ex-open identifies entitlement; no record-date inference, due bills,
DRIP, ADR fees, FX, return of capital or stock distributions are implemented.
Historical coverage, issuer identity and broker-credit clocks belong to inputs.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.equity_wallets import EquityCashContract, _aware


def _sha(value):
    if not isinstance(value, str) or len(value) != 64 or any(
        c not in "0123456789abcdef" for c in value
    ):
        raise EquityAdmissionError("Dividend source bytes require SHA256")


@dataclass(frozen=True)
class CashDividend:
    identity: str
    pair: str
    ex_open: datetime
    announced_at: datetime
    gross_per_share: Decimal
    withholding_rate: Decimal
    tax_known_at: datetime
    source_sha256: str
    tax_source_sha256: str
    available_at: datetime | None = None
    payment_known_at: datetime | None = None
    payment_source_sha256: str | None = None
    kind: str = "ordinary_cash"
    quantum: Decimal = Decimal(".01")
    rounding: str = "HALF_UP"

    def __post_init__(self):
        for value in (self.ex_open, self.announced_at, self.tax_known_at):
            _aware(value)
        if not isinstance(self.identity, str) or not self.identity or self.kind != "ordinary_cash":
            raise EquityAdmissionError("Only explicit ordinary cash dividend identities supported")
        if self.announced_at >= self.ex_open or self.tax_known_at >= self.ex_open:
            raise EquityAdmissionError("Dividend and tax terms must be known before ex-open")
        for value in (self.gross_per_share, self.withholding_rate, self.quantum):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise EquityAdmissionError("Finite Decimal dividend amounts and tax required")
        if self.gross_per_share <= 0 or not 0 <= self.withholding_rate <= 1:
            raise EquityAdmissionError("Invalid dividend amount or withholding rate")
        if self.quantum != Decimal(".01") or self.rounding != "HALF_UP":
            raise EquityAdmissionError("Only explicit cent HALF_UP entitlement rounding supported")
        _sha(self.source_sha256)
        _sha(self.tax_source_sha256)
        if self.available_at is None:
            if self.payment_known_at is not None or self.payment_source_sha256 is not None:
                raise EquityAdmissionError("Unknown payment cannot have a partial payment bundle")
        elif self.payment_known_at is None:
            raise EquityAdmissionError("Payment availability needs a knowledge clock")
        else:
            _aware(self.available_at)
            _aware(self.payment_known_at)
            _sha(self.payment_source_sha256)
            if self.available_at < self.ex_open or self.payment_known_at > self.available_at:
                raise EquityAdmissionError("Invalid dividend payment clocks")

    def amounts(self, quantity):
        if not isinstance(quantity, Decimal) or not quantity.is_finite() or quantity < 0:
            raise EquityAdmissionError("Nonnegative finite actual entitlement quantity required")
        gross = (quantity * self.gross_per_share).quantize(self.quantum, rounding=ROUND_HALF_UP)
        tax = (gross * self.withholding_rate).quantize(self.quantum, rounding=ROUND_HALF_UP)
        return gross, tax, gross - tax


@dataclass(frozen=True)
class DividendContract:
    cash: EquityCashContract
    pairs: tuple[str, ...]
    coverage_start: datetime
    coverage_end: datetime
    events: tuple[CashDividend, ...]
    source_sha256: str
    price_basis: str = "nominal_unadjusted"

    def __post_init__(self):
        if not isinstance(self.cash, EquityCashContract):
            raise EquityAdmissionError("Dividend cash calendar required")
        _aware(self.coverage_start)
        _aware(self.coverage_end)
        _sha(self.source_sha256)
        if self.coverage_start >= self.coverage_end or self.price_basis != "nominal_unadjusted":
            raise EquityAdmissionError("Explicit nominal-price dividend coverage required")
        if (
            not isinstance(self.pairs, tuple) or not isinstance(self.events, tuple)
            or not self.pairs or len(set(self.pairs)) != len(self.pairs)
        ) or any(
            not isinstance(p, str) or not p.endswith("/" + self.cash.currency) for p in self.pairs
        ):
            raise EquityAdmissionError("Unique same-currency covered securities required")
        identities, keys = set(), set()
        opens = {s.opens for s in self.cash.sessions}
        for event in self.events:
            if not isinstance(event, CashDividend):
                raise EquityAdmissionError("Explicit dividend event required")
            key = (event.pair, event.ex_open)
            if event.identity in identities or key in keys:
                raise EquityAdmissionError("Duplicate dividend identity or ex-open")
            identities.add(event.identity)
            keys.add(key)
            if event.pair not in self.pairs or event.ex_open not in opens:
                raise EquityAdmissionError("Dividend must bind covered security and exact ex-open")
            if not self.coverage_start < event.ex_open <= self.coverage_end:
                raise EquityAdmissionError("Dividend entitlement outside coverage")

    def require_clock(self, asof):
        if not self.coverage_start <= _aware(asof) <= self.coverage_end:
            raise EquityAdmissionError("Dividend input coverage does not include account clock")
