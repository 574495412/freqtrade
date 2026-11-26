"""Local, backtest-only settled-cash restriction on native Freqtrade wallets.

Trade/Order and Wallets still own fills, cost basis and realized PnL. This
extension only partitions native quote cash and reserves unpaid entry fees.
It does not enable the incomplete EquityBacktesting account entry point.
"""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import fsum, isclose, isfinite
from zoneinfo import ZoneInfo

from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.enums import RunMode, TradingMode
from freqtrade.persistence import Trade
from freqtrade.wallets import Wallets


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise EquityAdmissionError("Explicit timezone-aware cash clock required")
    return value


@dataclass(frozen=True)
class CashSession:
    label: date
    opens: datetime
    closes: datetime
    break_start: datetime | None = None
    break_end: datetime | None = None


@dataclass(frozen=True)
class SettlementDay:
    label: date
    available_at: datetime


@dataclass(frozen=True)
class EquityCashContract:
    """Separate, explicitly supplied trading and cash-settlement calendars.

    The caller owns completeness/source evidence and broker release assumptions.
    Exchange trading sessions are never silently reused as settlement days.
    Effective dates bound the supported trade-date regime, not research returns.
    """

    market: str
    currency: str
    effective_start: date
    effective_end: date
    sessions: tuple[CashSession, ...]
    settlement_days: tuple[SettlementDay, ...]
    source_sha256: str

    def __post_init__(self):
        if (self.market, self.currency) not in {("XNYS", "USD"), ("XHKG", "HKD")}:
            raise EquityAdmissionError("Separate USD/XNYS or HKD/XHKG cash account required")
        if not self.sessions or not self.settlement_days:
            raise EquityAdmissionError("Explicit trading AND settlement calendars required")
        if self.effective_start > self.effective_end:
            raise EquityAdmissionError("Invalid effective-date interval")
        if self.market == "XNYS" and self.effective_start < date(2024, 5, 28):
            raise EquityAdmissionError("US pre-T+1 regime is unsupported")
        if len(self.source_sha256) != 64 or any(
            c not in "0123456789abcdef" for c in self.source_sha256
        ):
            raise EquityAdmissionError("Bind the supplied calendar/source contract by SHA256")
        for rows in (self.sessions, self.settlement_days):
            labels = [r.label for r in rows]
            if labels != sorted(set(labels)) or any(type(d) is not date for d in labels):
                raise EquityAdmissionError("Calendar dates must be unique, increasing dates")
        self._validate_sessions()
        self._validate_releases()

    def _validate_sessions(self):
        zone = ZoneInfo(self.timezone)
        for s in self.sessions:
            if not self.effective_start <= s.label <= self.effective_end:
                raise EquityAdmissionError("Trade session outside effective regime")
            if _aware(s.opens) >= _aware(s.closes) or s.opens.astimezone(zone).date() != s.label:
                raise EquityAdmissionError("Invalid session clock")
            if s.closes.astimezone(zone).date() != s.label:
                raise EquityAdmissionError("Overnight trading is unsupported")
            if (s.break_start is None) != (s.break_end is None):
                raise EquityAdmissionError("Both lunch-break endpoints required")
            if s.break_start is not None and not (
                s.opens < _aware(s.break_start) < _aware(s.break_end) < s.closes
            ):
                raise EquityAdmissionError("Invalid lunch break")

    def _validate_releases(self):
        zone = ZoneInfo(self.timezone)
        releases = []
        for day in self.settlement_days:
            release = _aware(day.available_at)
            if release.astimezone(zone).date() < day.label:
                raise EquityAdmissionError("Cash cannot be released before settlement date")
            releases.append(release)
        if releases != sorted(releases):
            raise EquityAdmissionError("Settlement release clocks must be increasing")

    @property
    def timezone(self):
        return "America/New_York" if self.market == "XNYS" else "Asia/Hong_Kong"

    @property
    def lag(self):
        return 1 if self.market == "XNYS" else 2

    def release_for_fill(self, filled_at: datetime) -> SettlementDay:
        when = _aware(filled_at)
        label = when.astimezone(ZoneInfo(self.timezone)).date()
        session = next((s for s in self.sessions if s.label == label), None)
        if session is None or not session.opens <= when <= session.closes:
            raise EquityAdmissionError("Fill outside declared cash trading session")
        if session.break_start is not None and session.break_start <= when < session.break_end:
            raise EquityAdmissionError("Fill during declared lunch break")
        labels = [d.label for d in self.settlement_days]
        offset = bisect_right(labels, label) + self.lag - 1
        if offset >= len(labels):
            raise EquityAdmissionError("Settlement calendar does not cover full trade tail")
        return self.settlement_days[offset]


@dataclass(frozen=True)
class NativeCashRestriction:
    unsettled_sale_proceeds: float
    open_entry_fee_reserve: float
    sale_orders: tuple[tuple[str, str, datetime, float], ...]
    filled_order_cash_flow: float = 0.0


def _validate_native_trade(trade, contract, asof, entry_fee_rate):
    if (
        trade.is_short
        or trade.leverage != 1
        or trade.trading_mode != TradingMode.SPOT
        or trade.pair.split("/")[-1] != contract.currency
    ):
        raise EquityAdmissionError("Only unlevered, single-currency long cash trades supported")
    if trade.open_date_utc > asof:
        raise EquityAdmissionError("Native state contains future trades")
    if not isclose(trade.fee_open, entry_fee_rate, rel_tol=0, abs_tol=1e-14):
        raise EquityAdmissionError("Native entry fee differs from cash sizing contract")
    if not isfinite(trade.fee_close) or not 0 <= trade.fee_close < 1:
        raise EquityAdmissionError("Explicit finite quote exit fee required")
    if not isfinite(trade.stake_amount) or trade.stake_amount < 0:
        raise EquityAdmissionError("Invalid native remaining cost basis")


def _validate_filled_order(order, asof):
    filled_at = order.order_filled_utc
    if filled_at is None or filled_at > asof:
        raise EquityAdmissionError("Missing/future native fill clock")
    if order.order_filled_date.utcoffset() not in (None, timedelta(0)):
        raise EquityAdmissionError("Native stored fill clocks must be UTC")
    if order.ft_is_open or order.status != "closed" or order.safe_remaining:
        raise EquityAdmissionError("Only atomic native backtest fills supported")
    if order.safe_fee_base or order.funding_fee:
        raise EquityAdmissionError("Base-currency fees/funding unsupported in cash restriction")
    if not isfinite(order.safe_price) or order.safe_price <= 0:
        raise EquityAdmissionError("Invalid native filled price")
    return filled_at


def native_cash_restriction(
    contract: EquityCashContract, asof: datetime, entry_fee_rate: float
) -> NativeCashRestriction:
    """Read actual native state; never create fills, profits, holdings or NAV."""
    _aware(asof)
    claims, reserves, flows, seen = [], [], [], set()
    for trade in Trade.get_trades_proxy():
        _validate_native_trade(trade, contract, asof, entry_fee_rate)
        if trade.is_open:
            reserves.append(trade.stake_amount * entry_fee_rate)
        for order in trade.orders:
            key = (trade.pair, order.order_id)
            if key in seen or not order.order_id:
                raise EquityAdmissionError("Duplicate native order identity")
            seen.add(key)
            if not isfinite(order.safe_filled) or order.safe_filled < 0:
                raise EquityAdmissionError("Invalid native filled quantity")
            if not order.safe_filled:
                if order.ft_is_open:
                    raise EquityAdmissionError(
                        "Cash checks require completed native fill boundaries"
                    )
                continue
            filled_at = _validate_filled_order(order, asof)
            release = contract.release_for_fill(filled_at)
            if order.ft_order_side == trade.entry_side:
                flows.append(-order.safe_filled * order.safe_price * (1 + entry_fee_rate))
                continue
            if order.ft_order_side != trade.exit_side:
                raise EquityAdmissionError("Unrecognized native cash fill side")
            proceeds = order.safe_filled * order.safe_price * (1 - trade.fee_close)
            flows.append(proceeds)
            if asof < release.available_at:
                claims.append((trade.pair, order.order_id, release.available_at, proceeds))
    return NativeCashRestriction(
        fsum(c[3] for c in claims), fsum(reserves), tuple(claims), fsum(flows)
    )


class EquitySettlementWallets(Wallets):
    """Explicit research-only native subclass; ordinary Wallets are unchanged.

    Cash-only policy: no reuse of unsettled proceeds. This conservative policy
    is not a statement that every broker disallows all such purchases. Entry
    sizing includes the declared constant quote commission. Side-specific taxes,
    tiered fees, dividends and corporate actions require further integration.
    """

    def __init__(
        self,
        config,
        exchange,
        *,
        contract: EquityCashContract,
        asof: datetime,
        entry_fee_rate: float,
    ):
        self._check_isolation(config, contract)
        if not isfinite(entry_fee_rate) or not 0 <= entry_fee_rate < 1:
            raise EquityAdmissionError("Explicit finite entry commission required")
        self.contract, self.asof, self.entry_fee_rate = contract, _aware(asof), entry_fee_rate
        self.cash_restriction = NativeCashRestriction(0, 0, ())
        super().__init__(config, exchange, is_backtest=True)

    @staticmethod
    def _check_isolation(config, contract):
        if (
            config.get("dry_run") is not True
            or config.get("runmode") != RunMode.BACKTEST
            or config.get("trading_mode", "spot") != TradingMode.SPOT
            or Trade.use_db
            or config.get("stake_currency") != contract.currency
        ):
            raise EquityAdmissionError("Settlement wallet is isolated BACKTEST only")
        capital = config.get("dry_run_wallet")
        if isinstance(capital, dict):
            if set(capital) != {contract.currency}:
                raise EquityAdmissionError("Initial cash must contain only the account currency")
            capital = capital[contract.currency]
        if isinstance(capital, bool) or not isinstance(capital, (int, float)):
            raise EquityAdmissionError("Explicit finite positive initial cash required")
        if not isfinite(capital) or capital <= 0:
            raise EquityAdmissionError("Explicit finite positive initial cash required")

    def update(self, require_update=True):
        self._check_isolation(self._config, self.contract)
        return super().update(require_update)

    def _update_live(self):
        raise EquityAdmissionError("Settlement wallet cannot read live balances")

    def _additional_settled_cash(self):
        """Cumulative non-order cash, default zero; owning overlay supplies evidence.

        This hook cannot turn unsettled receivables into spendable cash. It is
        recomputed at each native boundary, never added to initial capital.
        """
        return 0.0

    def advance(self, asof: datetime):
        _aware(asof)
        if asof < self.asof:
            raise EquityAdmissionError("Native cash clock cannot move backwards")
        previous = self.asof
        self.asof = asof
        try:
            self.update()
        except Exception:
            self.asof = previous
            raise

    def _update_dry(self):
        restriction = native_cash_restriction(self.contract, self.asof, self.entry_fee_rate)
        previous_wallets, previous_positions = self._wallets, self._positions
        try:
            super()._update_dry()
            balance = self._wallets[self._stake_currency]
            credit = self._additional_settled_cash()
            if isinstance(credit, bool) or not isfinite(credit) or credit < 0:
                raise EquityAdmissionError("Invalid cumulative additional settled cash")
            cash = balance.free - restriction.open_entry_fee_reserve + credit
            expected = (
                self._start_cap[self._stake_currency] + restriction.filled_order_cash_flow + credit
            )
            if not isclose(cash + balance.used, expected, rel_tol=0, abs_tol=1e-8):
                raise EquityAdmissionError("Native cost basis/cash needs further reconciliation")
            free = cash - restriction.unsettled_sale_proceeds
            if free < -1e-8:
                raise EquityAdmissionError(
                    "Native fills overspent settled cash including entry fees"
                )
            self._wallets[self._stake_currency] = balance._replace(
                free=max(free, 0.0),
                used=balance.used + restriction.unsettled_sale_proceeds,
                total=balance.total - restriction.open_entry_fee_reserve + credit,
            )
        except Exception:
            self._wallets, self._positions = previous_wallets, previous_positions
            raise
        self.cash_restriction = restriction

    def get_available_stake_amount(self):
        return max(0.0, super().get_available_stake_amount()) / (1 + self.entry_fee_rate)
