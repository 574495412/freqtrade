"""Local inverse-option unit admission, not an exchange or funded account.

Deribit inverse options quote and settle in base coin; strikes are USD/base.
API amounts are base-coin exposure. Current supported multipliers are exactly1.
Fee parameters are explicit inputs, never inferred historical/private fee tiers.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit


class OptionAdmissionError(ValueError):
    """The declared option input/account contract is unsupported or inconsistent."""


def aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise OptionAdmissionError("Timezone-aware option clock required")
    try:
        offset = value.utcoffset()
    except (TypeError, ValueError, OverflowError) as error:
        raise OptionAdmissionError("Invalid option clock") from error
    if offset is None:
        raise OptionAdmissionError("Timezone-aware option clock required")
    return value


def decimal(value, *, positive=False):
    if not isinstance(value, Decimal) or not value.is_finite():
        raise OptionAdmissionError("Finite Decimal option amount required")
    if positive and value <= 0:
        raise OptionAdmissionError("Positive option amount required")
    return value


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise OptionAdmissionError("Original JSON numeric field required")
    return decimal(Decimal(value), positive=True)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OptionAdmissionError("Duplicate original JSON field")
        result[key] = value
    return result


def _instrument_name(base, expiry, strike, option_type):
    months = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
    expiry = expiry.astimezone(UTC)
    label = f"{expiry.day}{months[expiry.month - 1]}{expiry.year % 100:02d}"
    text = format(strike, "f")
    strike_text = text.rstrip("0").rstrip(".") if "." in text else text
    return f"{base}-{label}-{strike_text}-{'C' if option_type == 'call' else 'P'}"


def _validate_tiers(tiers):
    if not isinstance(tiers, tuple):
        raise OptionAdmissionError("Immutable original tick tiers required")
    previous = Decimal(0)
    for tier in tiers:
        if not isinstance(tier, tuple) or len(tier) != 2:
            raise OptionAdmissionError("Explicit threshold/tick pair required")
        above, tick = tier
        decimal(above, positive=True)
        decimal(tick, positive=True)
        if above <= previous:
            raise OptionAdmissionError("Strictly increasing tick thresholds required")
        previous = above


def read_response(raw, *, source_url, http_status, endpoint):
    if not isinstance(source_url, str):
        raise OptionAdmissionError("Exact public API source URL required")
    try:
        url = urlsplit(source_url)
        query = parse_qs(url.query)
    except ValueError as error:
        raise OptionAdmissionError("Invalid original API URL") from error
    if (
        url.scheme != "https"
        or url.hostname != "www.deribit.com"
        or url.username
        or url.password
        or url.fragment
        or url.path != "/api/v2/public/" + endpoint
        or http_status != 200
        or type(raw) is not bytes
        or not raw
    ):
        raise OptionAdmissionError("Successful exact Deribit public response required")
    try:
        response = json.loads(raw, parse_float=Decimal, object_pairs_hook=_unique)
    except (ValueError, UnicodeError) as error:
        raise OptionAdmissionError("Invalid original option JSON") from error
    if not isinstance(response, dict) or response.get("error") is not None:
        raise OptionAdmissionError("Option API error is not market data")
    if response.get("jsonrpc") != "2.0" or "result" not in response:
        raise OptionAdmissionError("Original JSON-RPC result required")
    return response["result"], query


@dataclass(frozen=True)
class InverseOptionSpec:
    instrument_name: str
    instrument_id: int
    base_currency: str
    option_type: str
    strike_usd_per_base: Decimal
    expiration_at: datetime
    creation_at: datetime
    min_amount_base: Decimal
    tick_size: Decimal
    tick_steps: tuple[tuple[Decimal, Decimal], ...]
    snapshot_sha256: str
    observed_at: datetime

    def __post_init__(self):
        if self.base_currency not in {"BTC", "ETH"} or self.option_type not in {"call", "put"}:
            raise OptionAdmissionError("Explicit inverse coin and call/put type required")
        if type(self.instrument_id) is not int or self.instrument_id <= 0:
            raise OptionAdmissionError("Exact positive instrument id required")
        for value in (self.strike_usd_per_base, self.min_amount_base, self.tick_size):
            decimal(value, positive=True)
        for value in (self.expiration_at, self.creation_at, self.observed_at):
            aware(value)
        if self.creation_at >= self.expiration_at or self.observed_at < self.creation_at:
            raise OptionAdmissionError("Invalid option creation/expiry/observation ordering")
        expiry = self.expiration_at.astimezone(UTC)
        if (expiry.hour, expiry.minute, expiry.second, expiry.microsecond) != (8, 0, 0, 0):
            raise OptionAdmissionError("Explicit08UTC option expiry required")
        if self.instrument_name != _instrument_name(
            self.base_currency, expiry, self.strike_usd_per_base, self.option_type
        ):
            raise OptionAdmissionError("Exact option name/type/strike/expiry identity required")
        if not isinstance(self.snapshot_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.snapshot_sha256
        ):
            raise OptionAdmissionError("Original metadata byte SHA required")
        _validate_tiers(self.tick_steps)

    @property
    def storage_pair(self):
        """Local native storage key, not a supported CCXT/spot trading pair."""
        return self.instrument_name + "/" + self.base_currency

    def tick_for(self, premium_base_per_base):
        price = decimal(premium_base_per_base, positive=True)
        tick = self.tick_size
        for above, step in self.tick_steps:
            if price > above:
                tick = step
        return tick

    def validate_known_order_limits(self, amount_base, premium_base_per_base):
        amount = decimal(amount_base, positive=True)
        price = decimal(premium_base_per_base, positive=True)
        if amount < self.min_amount_base or price % self.tick_for(price):
            raise OptionAdmissionError("Below minimum amount or invalid premium tick")
        # API lot_size is reserved, not a minimum/increment. No quantity-step,
        # venue acceptance, active-state or margin certification is implied.


def parse_instrument_response(raw, *, source_url, http_status, observed_at):
    aware(observed_at)
    if not isinstance(source_url, str):
        raise OptionAdmissionError("Original metadata source URL required")
    try:
        endpoint = urlsplit(source_url).path.rsplit("/", 1)[-1]
    except ValueError as error:
        raise OptionAdmissionError("Invalid original metadata source URL") from error
    if endpoint not in {"get_instrument", "get_instruments"}:
        raise OptionAdmissionError("Explicit instrument metadata endpoint required")
    result, query = read_response(
        raw, source_url=source_url, http_status=http_status, endpoint=endpoint
    )
    records = _metadata_records(result, query, endpoint)
    specs, names, ids = [], set(), set()
    for row in records:
        spec = _metadata_spec(row, raw, observed_at)
        if endpoint == "get_instruments" and query["currency"] != [spec.base_currency]:
            raise OptionAdmissionError("Metadata currency disagrees with original request")
        if spec.instrument_name in names or spec.instrument_id in ids:
            raise OptionAdmissionError("Duplicate instrument identity")
        names.add(spec.instrument_name)
        ids.add(spec.instrument_id)
        specs.append(spec)
    return tuple(specs)


def _metadata_records(result, query, endpoint):
    if endpoint == "get_instrument":
        if not isinstance(result, dict) or query.get("instrument_name") != [
            result.get("instrument_name")
        ]:
            raise OptionAdmissionError("Requested exact instrument must match metadata")
        records = [result]
    else:
        if (
            not isinstance(result, list)
            or not result
            or query.get("kind") != ["option"]
            or query.get("currency") not in (["BTC"], ["ETH"])
        ):
            raise OptionAdmissionError("Explicit BTC/ETH option metadata list required")
        records = result
    return records


def _metadata_spec(row, raw, observed_at):
    if not isinstance(row, dict):
        raise OptionAdmissionError("Original instrument object required")
    base = row.get("base_currency")
    if (
        base not in {"BTC", "ETH"}
        or row.get("kind") != "option"
        or row.get("instrument_type") != "reversed"
        or any(row.get(k) != base for k in ("quote_currency", "settlement_currency"))
        or row.get("counter_currency") != "USD"
        or _number(row.get("contract_size")) != 1
        or row.get("option_type") not in {"call", "put"}
    ):
        raise OptionAdmissionError("Only multiplier1 coin-quoted inverse BTC/ETH options supported")
    if type(row.get("instrument_id")) is not int or row["instrument_id"] <= 0:
        raise OptionAdmissionError("Exact positive instrument id required")
    for field in ("creation_timestamp", "expiration_timestamp"):
        if type(row.get(field)) is not int or row[field] <= 0:
            raise OptionAdmissionError("Original millisecond instrument clocks required")
    try:
        expiry = datetime.fromtimestamp(row["expiration_timestamp"] / 1000, UTC)
        created = datetime.fromtimestamp(row["creation_timestamp"] / 1000, UTC)
    except (ValueError, OverflowError, OSError) as error:
        raise OptionAdmissionError("Invalid original instrument epoch") from error
    if created >= expiry or (expiry.hour, expiry.minute, expiry.second, expiry.microsecond) != (
        8,
        0,
        0,
        0,
    ):
        raise OptionAdmissionError("Supported expiry is the explicit08UTC clock after creation")
    strike = _number(row.get("strike"))
    expected = _instrument_name(base, expiry, strike, row["option_type"])
    if row.get("instrument_name") != expected:
        raise OptionAdmissionError("Instrument name/strike/type/expiry identity conflict")
    steps = row.get("tick_size_steps")
    if not isinstance(steps, list):
        raise OptionAdmissionError("Explicit original premium tick steps required")
    if any(not isinstance(r, dict) or not {"above_price", "tick_size"} <= r.keys() for r in steps):
        raise OptionAdmissionError("Original threshold/tick objects required")
    parsed = tuple((_number(r["above_price"]), _number(r["tick_size"])) for r in steps)
    return InverseOptionSpec(
        expected,
        row["instrument_id"],
        base,
        row["option_type"],
        strike,
        expiry,
        created,
        _number(row.get("min_trade_amount")),
        _number(row.get("tick_size")),
        parsed,
        hashlib.sha256(raw).hexdigest(),
        observed_at,
    )


@dataclass(frozen=True)
class BaseCoinAmount:
    currency: str
    amount: Decimal

    def __post_init__(self):
        if self.currency not in {"BTC", "ETH"}:
            raise OptionAdmissionError("Explicit BTC/ETH amount currency required")
        decimal(self.amount)


@dataclass(frozen=True)
class IsolatedExpiry:
    intrinsic_before_fees: BaseCoinAmount
    route: str
    generated_futures_notional_usd: Decimal


@dataclass(frozen=True)
class OptionDeliveryPrice:
    instrument_name: str
    index_name: str
    delivery_at: datetime
    price_usd_per_base: Decimal
    source_sha256: str
    observed_at: datetime


def parse_delivery_response(raw, *, source_url, http_status, observed_at, spec):
    """One actual closed expiry day,not a spot candle or forecast settlement."""
    aware(observed_at)
    if not isinstance(spec, InverseOptionSpec) or observed_at < spec.expiration_at:
        raise OptionAdmissionError("Actual closed option expiry and observation required")
    result, query = read_response(
        raw, source_url=source_url, http_status=http_status, endpoint="get_delivery_prices"
    )
    index = spec.base_currency.lower() + "_usd"
    target_date = spec.expiration_at.astimezone(UTC).date().isoformat()
    if query.get("index_name") != [index]:
        raise OptionAdmissionError("Exact option delivery index required")
    for key in ("start_timestamp", "end_timestamp"):
        values = query.get(key)
        if not values or len(values) != 1 or not values[0].isdigit():
            raise OptionAdmissionError("Explicit single delivery-day request bounds required")
        try:
            date = datetime.fromtimestamp(int(values[0]) / 1000, UTC).date().isoformat()
        except (ValueError, OverflowError, OSError) as error:
            raise OptionAdmissionError("Invalid delivery request epoch") from error
        if date != target_date:
            raise OptionAdmissionError("Requested delivery day differs from option expiry")
    if (
        not isinstance(result, dict)
        or result.get("records_total") != 1
        or not isinstance(result.get("data"), list)
        or len(result["data"]) != 1
    ):
        raise OptionAdmissionError("One complete actual expiry-price record required")
    row = result["data"][0]
    if not isinstance(row, dict) or row.get("date") != target_date:
        raise OptionAdmissionError("Original delivery day must equal exact expiry day")
    return OptionDeliveryPrice(
        spec.instrument_name,
        index,
        spec.expiration_at,
        _number(row.get("delivery_price")),
        hashlib.sha256(raw).hexdigest(),
        observed_at,
    )


def isolated_expiry(spec, *, signed_amount_base, delivery_price_usd_per_base):
    """Gross isolated intrinsic only;not premium PnL,delivery fees or mixed-book netting."""
    if not isinstance(spec, InverseOptionSpec):
        raise OptionAdmissionError("Admitted inverse option specification required")
    amount = decimal(signed_amount_base)
    price = decimal(delivery_price_usd_per_base, positive=True)
    difference = price - spec.strike_usd_per_base
    intrinsic = max(difference if spec.option_type == "call" else -difference, Decimal(0))
    value = BaseCoinAmount(spec.base_currency, amount * intrinsic / price)
    # Current Deribit documentation states this explicit effective expiry date.
    # This classification does not certify every historical policy/transaction.
    physical = spec.expiration_at >= datetime(2026, 8, 1, tzinfo=UTC)
    future = Decimal(0)
    if physical and intrinsic > 0:
        future = amount * spec.strike_usd_per_base * (1 if spec.option_type == "call" else -1)
    return IsolatedExpiry(value, "inverse_future_then_coin" if physical else "direct_coin", future)


def option_trade_fee(spec, *, amount_base, premium_base_per_base, underlying_rate, premium_cap):
    """Explicit one-fill arithmetic;no tier/discount/rounding/delivery-fee assumption."""
    if not isinstance(spec, InverseOptionSpec):
        raise OptionAdmissionError("Admitted inverse option specification required")
    amount = decimal(amount_base, positive=True)
    premium = decimal(premium_base_per_base, positive=True)
    rate, cap = decimal(underlying_rate), decimal(premium_cap)
    if not 0 <= rate <= 1 or not 0 <= cap <= 1:
        raise OptionAdmissionError("Explicit nonnegative fee and premium-cap fractions required")
    return BaseCoinAmount(spec.base_currency, amount * min(rate, premium * cap))


def require_complete_option_account_support():
    raise OptionAdmissionError(
        "Option contract/history admission only: native option positions,premium coin cash,"
        "collateral/margin,hedge fills,dated trade/delivery fees,expiry legs and full terminal "
        "account NAV are not integrated. No complete option return is certified."
    )
