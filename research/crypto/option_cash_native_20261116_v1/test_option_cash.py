"""Source margin arithmetic plus native cash/fill integrity boundaries."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from native_probe import END, START, build, marks, native_option_notional, row, specs

from freqtrade.data.inverse_option_contracts import OptionAdmissionError
from freqtrade.data.option_cash import OptionCashContract, OptionMark, option_margin
from freqtrade.enums import RunMode, TradingMode
from freqtrade.option_wallets import OptionPremiumWallets
from freqtrade.persistence import LocalTrade, Trade


D = Decimal


@pytest.fixture(autouse=True)
def isolated():
    old = Trade.use_db
    Trade.use_db = False
    LocalTrade.reset_trades()
    with patch.object(Trade, "commit"):
        yield
    LocalTrade.reset_trades()
    Trade.use_db = old


def contract():
    return OptionCashContract("BTC", specs("BTC"), START, END, D(".0003"), D(".125"), "a" * 64)


def config(**changes):
    return dict(
        option_wallet_component=True,
        dry_run=True,
        runmode=RunMode.BACKTEST,
        trading_mode=TradingMode.FUTURES,
        stake_currency="BTC",
        dry_run_wallet=1.0,
        tradable_balance_ratio=1.0,
        **changes,
    )


def wallet():
    c = contract()
    return OptionPremiumWallets(
        config(),
        SimpleNamespace(get_proxy_coin=lambda: "BTC"),
        contract=c,
        asof=START,
        marks=marks(c, START),
    )


def test_empty_native_wallet_cash_equals_margin_and_equity():
    w = wallet()
    state = w.cash_state
    assert state.cash_balance == state.equity == state.margin_balance == 1
    assert state.initial_margin == state.execution_fees == state.pending_reserve == 0
    for _ in range(3):
        w.update()
        assert w.cash_state == state


@pytest.mark.parametrize(
    "field,value",
    [
        ("trade_fee_rate", D(-1)),
        ("premium_fee_cap", D("NaN")),
        ("margin_model", "PM"),
        ("pending_model", "anticipated_credit"),
        ("max_mark_age", timedelta(hours=1)),
        ("currency", "USDT"),
        ("ends", START),
        ("specs", ()),
    ],
)
def test_invalid_contract_is_not_silently_normalized(field, value):
    with pytest.raises(OptionAdmissionError):
        replace(contract(), **{field: value})


@pytest.mark.parametrize(
    "kind,qty,premium,initial,maintenance",
    [
        ("call", "-.1", ".05", ".020", ".0125"),
        ("put", "-.1", ".05", ".020", ".0125"),
        ("put", "-1", "10", "10.75", "10.75"),
        ("call", ".1", ".50", "0", "0"),
    ],
)
def test_current_source_margin_formulas(kind, qty, premium, initial, maintenance):
    s = next(s for s in contract().specs if s.option_type == kind)
    mark = OptionMark(s.storage_pair, START, START, D(premium), s.strike_usd_per_base, "b" * 64)
    assert option_margin(s, D(qty), mark) == (D(initial), D(maintenance))


def test_otm_call_initial_floor_and_long_gains_have_zero_margin_value():
    s = contract().specs[0]
    m = OptionMark(s.storage_pair, START, START, D(".01"), s.strike_usd_per_base / 2, "b" * 64)
    assert option_margin(s, D(-1), m) == (D(".11"), D(".085"))


@pytest.mark.parametrize("variation", ["future_known", "stale", "duplicate", "wrong_pair"])
def test_bad_marks_leave_clock_and_wallet_state_unchanged(variation):
    w = wallet()
    previous = w.cash_state
    supplied = marks(w.contract, START)
    if variation == "future_known":
        supplied = (replace(supplied[0], known_at=START + timedelta(seconds=1)),)
    elif variation == "stale":
        supplied = (replace(supplied[0], at=START - timedelta(minutes=2)),)
    elif variation == "duplicate":
        supplied = supplied + (supplied[0],)
    else:
        supplied = (replace(supplied[0], pair="BTC/USDT"),)
    with pytest.raises(OptionAdmissionError):
        w.advance(START, supplied)
    assert w.cash_state == previous and w.asof == START


def test_no_live_or_database_wallet_admission():
    c = contract()
    with pytest.raises(OptionAdmissionError):
        OptionPremiumWallets(
            config() | {"dry_run": False}, SimpleNamespace(), contract=c, asof=START
        )
    Trade.use_db = True
    with pytest.raises(OptionAdmissionError):
        OptionPremiumWallets(config(), SimpleNamespace(), contract=c, asof=START)
    Trade.use_db = False


@pytest.fixture
def native_account(tmp_path):
    account, exchange, minimum = build("BTC", "short", tmp_path / "account")
    try:
        yield account, exchange, minimum
    finally:
        account.cleanup()
        exchange.close()
        Trade.use_db = False


def native_entry(account, exchange, quantity):
    s = account.wallets.contract.specs[0]
    stake = native_option_notional(
        quantity, D(".05"), exchange.markets[s.storage_pair], account.precision_mode
    )
    result = account._enter_trade(
        s.storage_pair, row(START, D(".05")), "short", requested_rate=0.05, requested_stake=stake
    )
    account.wallets.update()
    return result


def test_native_quantity_adapter_prevents_half_quantity_rounding(native_account):
    a, exchange, minimum = native_account
    t = native_entry(a, exchange, minimum * 2)
    assert D(str(t.amount)) == minimum * 2
    assert len(a.wallets.fill_journal) == 1
    assert a.wallets.cash_state.equity == 1 - minimum * 2 * D(".0003")


@pytest.mark.parametrize(
    "fault", ["change_price", "remove_fill", "duplicate", "native_fee", "funding"]
)
def test_native_history_corruption_cannot_refund_premiums_or_fees(native_account, fault):
    a, exchange, minimum = native_account
    t = native_entry(a, exchange, minimum * 2)
    previous = a.wallets.cash_state
    if fault == "change_price":
        t.orders[0].average = 0.055
    elif fault == "remove_fill":
        t.orders.clear()
    elif fault == "duplicate":
        t.orders.append(t.orders[0])
    elif fault == "native_fee":
        t.fee_open = 0.001
    else:
        t.funding_fees = 0.001
    with pytest.raises(OptionAdmissionError):
        a.wallets.update()
    assert a.wallets.cash_state == previous


def test_missing_exposure_mark_and_backward_clock_are_rejected(native_account):
    a, exchange, minimum = native_account
    native_entry(a, exchange, minimum * 2)
    w, previous = a.wallets, a.wallets.cash_state
    with pytest.raises(OptionAdmissionError):
        w.advance(START, ())
    assert w.cash_state == previous
    with pytest.raises(OptionAdmissionError):
        w.advance(START - timedelta(seconds=1), ())


def test_fee_only_cash_shortfall_blocks_native_fill(native_account):
    a, exchange, minimum = native_account
    cfg = a.config | {"dry_run_wallet": float(minimum * D(".05"))}
    a.wallets = OptionPremiumWallets(
        cfg,
        exchange,
        contract=a.wallets.contract,
        asof=START,
        marks=marks(a.wallets.contract, START),
    )
    a.strategy.wallets = a.wallets
    with pytest.raises(OptionAdmissionError, match="exceeds option cash"):
        s = a.wallets.contract.specs[0]
        stake = native_option_notional(
            minimum, D(".05"), exchange.markets[s.storage_pair], a.precision_mode
        )
        a._enter_trade(
            s.storage_pair, row(START, D(".05")), "long", requested_rate=0.05, requested_stake=stake
        )
    assert not a.wallets.fill_journal
    assert all(o.safe_filled == 0 for t in Trade.get_trades_proxy() for o in t.orders)


def test_full_historical_option_entry_stays_closed():
    from freqtrade.optimize.option_backtesting import OptionBacktesting

    with pytest.raises(OptionAdmissionError, match="No complete option return"):
        OptionBacktesting({})
