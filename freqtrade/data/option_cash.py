"""Explicit coin cash/mark/fee contracts for the local native option wallet.

Current Deribit S:SM formulas are a dated research model,not certified historical
rules. No futures hedges,cross collateral,portfolio margin or liquidations here.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from freqtrade.data.inverse_option_contracts import (
    InverseOptionSpec,
    OptionAdmissionError,
    aware,
    decimal,
    option_trade_fee,
)


def source_sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise OptionAdmissionError("Explicit source/fixture SHA256 required")


@dataclass(frozen=True)
class OptionMark:
    pair: str
    at: datetime
    known_at: datetime
    premium: Decimal
    index_usd: Decimal
    source_sha256: str

    def __post_init__(self):
        aware(self.at)
        aware(self.known_at)
        decimal(self.premium)
        decimal(self.index_usd, positive=True)
        source_sha(self.source_sha256)
        if self.premium < 0 or self.known_at < self.at:
            raise OptionAdmissionError("Nonnegative mark and non-backdated observation required")


@dataclass(frozen=True)
class OptionCashContract:
    currency: str
    specs: tuple[InverseOptionSpec, ...]
    starts: datetime
    ends: datetime
    trade_fee_rate: Decimal
    premium_fee_cap: Decimal
    source_sha256: str
    margin_model: str = "current_document_ssm_model"
    pending_model: str = "gross_requirement_no_unfilled_credit"
    max_mark_age: timedelta = timedelta(minutes=1)

    def __post_init__(self):
        aware(self.starts)
        aware(self.ends)
        source_sha(self.source_sha256)
        if self.currency not in {"BTC", "ETH"} or self.starts >= self.ends:
            raise OptionAdmissionError("Separate positive-window BTC/ETH cash contract required")
        for value in (self.trade_fee_rate, self.premium_fee_cap):
            decimal(value)
            if not 0 <= value <= 1:
                raise OptionAdmissionError("Explicit fee/cap fractions required")
        if (
            self.margin_model != "current_document_ssm_model"
            or self.pending_model != "gross_requirement_no_unfilled_credit"
            or self.max_mark_age != timedelta(minutes=1)
        ):
            raise OptionAdmissionError("Unsupported margin,pending or stale-mark contract")
        if not isinstance(self.specs, tuple) or not self.specs:
            raise OptionAdmissionError("Immutable admitted option coverage required")
        names, ids = set(), set()
        for spec in self.specs:
            if not isinstance(spec, InverseOptionSpec) or spec.base_currency != self.currency:
                raise OptionAdmissionError("Only same-coin inverse option coverage supported")
            if spec.storage_pair in names or spec.instrument_id in ids:
                raise OptionAdmissionError("Unique original option identity required")
            if not spec.creation_at <= self.starts < self.ends < spec.expiration_at:
                raise OptionAdmissionError("Cash component window must precede expiry")
            names.add(spec.storage_pair)
            ids.add(spec.instrument_id)

    def require_clock(self, at):
        if not self.starts <= aware(at) <= self.ends:
            raise OptionAdmissionError("Option cash clock outside declared coverage")

    def spec(self, pair):
        found = next((s for s in self.specs if s.storage_pair == pair), None)
        if found is None:
            raise OptionAdmissionError("Native position outside explicit option coverage")
        return found

    def fee(self, pair, at, quantity, price):
        self.require_clock(at)
        return option_trade_fee(
            self.spec(pair),
            amount_base=quantity,
            premium_base_per_base=price,
            underlying_rate=self.trade_fee_rate,
            premium_cap=self.premium_fee_cap,
        ).amount

    def marks(self, marks, at):
        self.require_clock(at)
        if not isinstance(marks, tuple):
            raise OptionAdmissionError("Immutable current option marks required")
        result = {}
        for mark in marks:
            if not isinstance(mark, OptionMark) or mark.pair in result:
                raise OptionAdmissionError("Unique typed option marks required")
            self.spec(mark.pair)
            if mark.known_at > at or mark.at > at or at - mark.at > self.max_mark_age:
                raise OptionAdmissionError("Missing,future or stale option mark")
            result[mark.pair] = mark
        return result


def option_margin(spec, signed_quantity, mark):
    """Current documented isolated-instrument S:SM requirements in base coin.

    Margin balance excludes ALL option values;IM below therefore includes the
    short mark component. Subtracting options value once more would double count.
    """
    decimal(signed_quantity)
    if mark.pair != spec.storage_pair:
        raise OptionAdmissionError("Margin mark must bind the exact option")
    if signed_quantity >= 0:
        return Decimal(0), Decimal(0)
    q, m, index, strike = -signed_quantity, mark.premium, mark.index_usd, spec.strike_usd_per_base
    if spec.option_type == "call":
        otm = max((strike - index) / index, Decimal(0))
        maintenance = Decimal(".075") + m
        initial = max(Decimal(".15") - otm, Decimal(".1")) + m
    else:
        otm = max(index - strike, Decimal(0)) / index
        maintenance = max(Decimal(".075"), Decimal(".075") * m) + m
        initial = max(max(Decimal(".15") - otm, Decimal(".1")) + m, maintenance)
    return q * initial, q * maintenance
