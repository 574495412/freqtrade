"""Local historical precision admission; no price or account simulation.

Callers supply security-specific board lots and effective-date evidence. The
HKEX helper covers ordinary equities in the declared 2025/2026 research window;
it is not an instrument master, a broker fill model, or an auction model.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from zoneinfo import ZoneInfo

from ccxt import TICK_SIZE

from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.equity_wallets import EquityCashContract
from freqtrade.exchange.exchange_utils import amount_to_precision, price_to_precision


def _number(value) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise EquityAdmissionError("Explicit finite positive execution number required") from exc
    if not result.is_finite() or result <= 0:
        raise EquityAdmissionError("Explicit finite positive execution number required")
    return result


@dataclass(frozen=True)
class SpreadBand:
    upper: Decimal
    tick: Decimal


@dataclass(frozen=True)
class SpreadTable:
    """First lower bound inclusive; upper bounds inclusive, in source units."""

    minimum: Decimal
    bands: tuple[SpreadBand, ...]

    def __post_init__(self):
        if not isinstance(self.minimum, Decimal) or not self.minimum.is_finite():
            raise EquityAdmissionError("Exact decimal spread bounds required")
        if self.minimum <= 0 or not self.bands or not isinstance(self.bands, tuple):
            raise EquityAdmissionError("Nonempty immutable spread table required")
        previous = self.minimum
        for band in self.bands:
            if not isinstance(band.upper, Decimal) or not isinstance(band.tick, Decimal):
                raise EquityAdmissionError("Exact decimal spread bounds required")
            if (
                not band.upper.is_finite()
                or not band.tick.is_finite()
                or band.upper <= previous
                or band.tick <= 0
                or band.upper % band.tick
            ):
                raise EquityAdmissionError("Invalid or unordered spread band")
            previous = band.upper
        if self.minimum % self.bands[0].tick:
            raise EquityAdmissionError("Minimum price is outside first tick grid")

    def tick_for(self, price) -> Decimal:
        value = _number(price)
        if value < self.minimum:
            raise EquityAdmissionError("Price below declared spread coverage")
        for band in self.bands:
            if value <= band.upper:
                return band.tick
        raise EquityAdmissionError("Price above declared spread coverage")

    def require_price(self, price) -> None:
        value = _number(price)
        tick = self.tick_for(value)
        native = price_to_precision(float(value), float(tick), TICK_SIZE)
        if value % tick or Decimal(str(native)) != value:
            raise EquityAdmissionError("Off-grid price: no synthetic rounding of a fill")


def hkex_equity_spreads(session: date) -> SpreadTable:
    """HKEX Applicable Securities table, explicitly limited to ordinary equities.

    Version selection uses the local trade date, not a current market snapshot.
    Security classification and board-lot history still require external inputs.
    """
    if type(session) is not date or not date(2025, 1, 1) <= session <= date(2026, 8, 31):
        raise EquityAdmissionError("HKEX spread helper outside declared research coverage")
    phase1 = session >= date(2025, 8, 4)
    phase2 = session >= date(2026, 8, 3)
    values = [
        ("0.25", "0.001"),
        ("0.50", "0.005"),
        ("10", "0.005" if phase2 else "0.010"),
        ("20", "0.010" if phase1 else "0.020"),
        ("50", "0.020" if phase1 else "0.050"),
        ("100", "0.050"),
        ("200", "0.100"),
        ("500", "0.200"),
        ("1000", "0.500"),
        ("2000", "1.000"),
        ("5000", "2.000"),
        ("9995", "5.000"),
    ]
    return SpreadTable(
        Decimal("0.01"), tuple(SpreadBand(Decimal(p), Decimal(t)) for p, t in values)
    )


@dataclass(frozen=True)
class SecurityExecutionRule:
    pair: str
    effective_start: date
    effective_end: date
    board_lot: int
    spreads: SpreadTable
    source_sha256: str
    security_type: str = "ordinary_equity"

    def __post_init__(self):
        if (
            type(self.effective_start) is not date
            or type(self.effective_end) is not date
            or self.effective_start > self.effective_end
            or type(self.board_lot) is not int
            or self.board_lot <= 0
            or not isinstance(self.spreads, SpreadTable)
            or self.security_type != "ordinary_equity"
            or not isinstance(self.pair, str)
            or len(self.pair.split("/")) != 2
            or not all(self.pair.split("/"))
            or len(self.source_sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.source_sha256)
        ):
            raise EquityAdmissionError("Explicit ordinary-equity rule and source SHA required")


@dataclass(frozen=True)
class EquityExecutionContract:
    cash: EquityCashContract
    rules: tuple[SecurityExecutionRule, ...]

    def __post_init__(self):
        if not isinstance(self.cash, EquityCashContract) or not self.rules:
            raise EquityAdmissionError(
                "Explicit cash calendar and historical security rules required"
            )
        if not isinstance(self.rules, tuple):
            raise EquityAdmissionError("Immutable historical security rules required")
        for pair in self.pairs:
            if pair.split("/")[-1] != self.cash.currency:
                raise EquityAdmissionError("Execution currency differs from cash account")
            periods = sorted(
                (r for r in self.rules if r.pair == pair), key=lambda r: r.effective_start
            )
            for previous, following in pairwise(periods):
                if previous.effective_end >= following.effective_start:
                    raise EquityAdmissionError("Overlapping security rule versions")
            for session in self.cash.sessions:
                if not any(r.effective_start <= session.label <= r.effective_end for r in periods):
                    raise EquityAdmissionError("Security rules do not cover every declared session")

    @property
    def pairs(self) -> tuple[str, ...]:
        return tuple(sorted({r.pair for r in self.rules}))

    def rule_for(self, pair: str, when: datetime) -> SecurityExecutionRule:
        # Includes timezone, local session/lunch, effective regime and settlement tail.
        self.cash.release_for_fill(when)
        label = when.astimezone(ZoneInfo(self.cash.timezone)).date()
        for rule in self.rules:
            if rule.pair == pair and rule.effective_start <= label <= rule.effective_end:
                return rule
        raise EquityAdmissionError("Unknown security or historical execution rule")

    def round_quantity(self, pair: str, when: datetime, proposed) -> float:
        rule = self.rule_for(pair, when)
        value = _number(proposed)
        native = amount_to_precision(float(value), rule.board_lot, TICK_SIZE)
        rounded = Decimal(str(native))
        if rounded < 0 or rounded > value or rounded % rule.board_lot:
            raise EquityAdmissionError("Native board-lot sizing failed conservative reconciliation")
        return native

    def require_fill(self, pair: str, when: datetime, quantity, price) -> None:
        rule = self.rule_for(pair, when)
        value = _number(quantity)
        if value % rule.board_lot or Decimal(str(self.round_quantity(pair, when, value))) != value:
            raise EquityAdmissionError("Off-lot fill: odd-lot and fractional execution unsupported")
        rule.spreads.require_price(price)
