"""Local, dated cash-equity fee contracts; not broker or account certification.

Each component is explicit on both sides. Caller-supplied calendars distinguish
trade dates from settlement dates. One atomic native order is one charging unit;
broker aggregation, rebates, non-quote fees and market-value stamp bases require
different contracts and are deliberately unsupported here.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, ROUND_UP, Decimal, InvalidOperation
from itertools import pairwise
from zoneinfo import ZoneInfo

from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.equity_wallets import EquityCashContract


def _decimal(value, *, positive=False):
    try:
        if isinstance(value, bool):
            raise ValueError
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or (positive and not result):
            raise ValueError
        return result
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise EquityAdmissionError("Finite nonnegative fee input required") from exc


@dataclass(frozen=True)
class DatedFeeRule:
    pair: str
    component: str
    side: str
    start: date
    end: date
    source_sha256: str
    clock: str = "trade_date"
    rate: Decimal = Decimal(0)
    per_share: Decimal = Decimal(0)
    fixed: Decimal = Decimal(0)
    minimum: Decimal = Decimal(0)
    maximum: Decimal | None = None
    quantum: Decimal = Decimal(".01")
    rounding: str = "HALF_UP"

    def __post_init__(self):
        if (
            not isinstance(self.pair, str)
            or len(self.pair.split("/")) != 2
            or not all(self.pair.split("/"))
            or not isinstance(self.component, str)
            or not self.component.strip()
            or self.side not in {"buy", "sell"}
            or type(self.start) is not date
            or type(self.end) is not date
            or self.start > self.end
            or self.clock not in {"trade_date", "settlement_date"}
            or self.rounding not in {"UP", "HALF_UP"}
            or not isinstance(self.source_sha256, str)
            or len(self.source_sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.source_sha256)
        ):
            raise EquityAdmissionError("Explicit dated, sourced, side-specific fee rule required")
        for field in ("rate", "per_share", "fixed", "minimum", "quantum"):
            value = getattr(self, field)
            if not isinstance(value, Decimal):
                raise EquityAdmissionError("Fee parameters must be exact Decimal values")
            _decimal(value, positive=field == "quantum")
        if self.maximum is not None and (
            not isinstance(self.maximum, Decimal) or _decimal(self.maximum) < self.minimum
        ):
            raise EquityAdmissionError("Fee cap cannot be below minimum")
        # Caps/minima must survive the declared rounding without violating limits.
        for value in (self.minimum, self.maximum):
            if value is not None and value % self.quantum:
                raise EquityAdmissionError("Fee bounds must align with rounding quantum")

    def charge(self, quantity, price) -> Decimal:
        q, p = _decimal(quantity, positive=True), _decimal(price, positive=True)
        fee = max(self.minimum, q * p * self.rate + q * self.per_share + self.fixed)
        if self.maximum is not None:
            fee = min(fee, self.maximum)
        mode = ROUND_UP if self.rounding == "UP" else ROUND_HALF_UP
        return (fee / self.quantum).to_integral_value(rounding=mode) * self.quantum


@dataclass(frozen=True)
class FeeQuote:
    total: Decimal
    components: tuple[tuple[str, Decimal, str], ...]


@dataclass(frozen=True)
class EquityFeeContract:
    cash: EquityCashContract
    pairs: tuple[str, ...]
    components: tuple[str, ...]
    rules: tuple[DatedFeeRule, ...]
    charging_unit: str = "atomic_order"

    def __post_init__(self):
        if (
            not isinstance(self.cash, EquityCashContract)
            or self.charging_unit != "atomic_order"
            or not all(
                isinstance(x, tuple) and x for x in (self.pairs, self.components, self.rules)
            )
            or len(set(self.pairs)) != len(self.pairs)
            or len(set(self.components)) != len(self.components)
            or any(not isinstance(r, DatedFeeRule) for r in self.rules)
        ):
            raise EquityAdmissionError("Explicit immutable per-order fee contract required")
        for rule in self.rules:
            if (
                rule.pair not in self.pairs
                or rule.pair.split("/")[-1] != self.cash.currency
                or rule.component not in self.components
            ):
                raise EquityAdmissionError("Foreign fee pair, currency or component")
        for pair in self.pairs:
            for component in self.components:
                for side in ("buy", "sell"):
                    versions = sorted(
                        (
                            r
                            for r in self.rules
                            if (r.pair, r.component, r.side) == (pair, component, side)
                        ),
                        key=lambda r: r.start,
                    )
                    if not versions or len({r.clock for r in versions}) != 1:
                        raise EquityAdmissionError(
                            "Both fee sides and one unambiguous clock required"
                        )
                    if any(a.end >= b.start for a, b in pairwise(versions)):
                        raise EquityAdmissionError("Overlapping fee versions")

    def quote(self, pair: str, when: datetime, side: str, quantity, price) -> FeeQuote:
        if pair not in self.pairs or side not in {"buy", "sell"}:
            raise EquityAdmissionError("Unknown fee security or side")
        _decimal(quantity, positive=True)
        _decimal(price, positive=True)
        release = self.cash.release_for_fill(when)
        trade_date = when.astimezone(ZoneInfo(self.cash.timezone)).date()
        values = []
        for component in self.components:
            selected = []
            for r in self.rules:
                label = trade_date if r.clock == "trade_date" else release.label
                if (r.pair, r.component, r.side) == (
                    pair,
                    component,
                    side,
                ) and r.start <= label <= r.end:
                    selected.append(r)
            if len(selected) != 1:
                raise EquityAdmissionError("Missing/ambiguous historical fee coverage")
            rule = selected[0]
            values.append((component, rule.charge(quantity, price), rule.source_sha256))
        return FeeQuote(sum((v[1] for v in values), Decimal(0)), tuple(values))

    def require_coverage(self) -> int:
        """Check every supplied trade session; no inference of historical completeness."""
        count = 0
        for session in self.cash.sessions:
            for pair in self.pairs:
                for side in ("buy", "sell"):
                    self.quote(pair, session.opens, side, 1, 1)
                    count += 1
        return count

    def affordable_quantity(self, pair, when, price, board_lot, cash) -> int:
        """Largest integral lot that fits gross plus all monotone buy charges."""
        p, budget = _decimal(price, positive=True), _decimal(cash)
        if type(board_lot) is not int or board_lot <= 0:
            raise EquityAdmissionError("Explicit positive integral board lot required")
        # Validate coverage even if no lot can be afforded.
        self.quote(pair, when, "buy", board_lot, p)
        low, high = 0, int(budget // (p * board_lot))
        if high * board_lot > 2**53:
            raise EquityAdmissionError("Quantity exceeds exact native float integer range")
        while low < high:
            middle = (low + high + 1) // 2
            q = middle * board_lot
            required = q * p + self.quote(pair, when, "buy", q, p).total
            if required <= budget:
                low = middle
            else:
                high = middle - 1
        return low * board_lot
