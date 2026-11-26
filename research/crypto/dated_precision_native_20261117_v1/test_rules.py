"""Synthetic rules exercise actual native price and Order construction paths."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from freqtrade.enums import MarginMode, RunMode, TradingMode
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.dated_price_precision import (
    DatedPricePrecisionMixin,
    PriceRule,
    PriceRuleBook,
)
from freqtrade.persistence import LocalTrade, Trade


PAIR = "RUNE/USDT:USDT"
AT = datetime(2026, 4, 29, 6, 30, tzinfo=UTC)
START, END = AT - timedelta(days=1), AT + timedelta(days=1)


class NativeDated(DatedPricePrecisionMixin, Backtesting):
    pass


@pytest.fixture(autouse=True)
def isolated_native_state():
    old = Trade.use_db
    Trade.use_db = False
    LocalTrade.reset_trades()
    yield
    LocalTrade.reset_trades()
    Trade.use_db = old


def rules():
    before = PriceRule(PAIR, START, AT, START, Decimal("0.01"), "a" * 64)
    after = PriceRule(PAIR, AT, END, START, Decimal("0.001"), "b" * 64)
    return PriceRuleBook((PAIR,), (before, after))


def engine(book=None):
    e = NativeDated.__new__(NativeDated)
    e.config = {
        "runmode": RunMode.BACKTEST,
        "dry_run": True,
        "research_dated_price_rules": True,
        "stake_currency": "USDT",
        "exchange": {"name": "binance", "pair_whitelist": [PAIR]},
    }
    e.trading_mode, e.margin_mode = TradingMode.FUTURES, MarginMode.ISOLATED
    e.precision_mode, e.precision_mode_price = 4, 4
    e.exchange = SimpleNamespace(
        precision_mode_price=4,
        get_max_leverage=lambda *a, **k: 1,
        get_min_pair_stake_amount=lambda *a, **k: 0,
        get_max_pair_stake_amount=lambda *a, **k: 10000,
        get_pair_base_currency=lambda p: "RUNE",
        get_contract_size=lambda p: 1,
        get_precision_amount=lambda p: 0.01,
    )
    e.wallets = SimpleNamespace(
        get_trade_stake_amount=lambda *a, **k: 100,
        get_available_stake_amount=lambda: 1000,
        validate_stake_amount=lambda **k: k["stake_amount"],
    )
    e.strategy = SimpleNamespace(
        order_types={"entry": "limit", "exit": "limit"},
        order_time_in_force={"entry": "GTC", "exit": "GTC"},
        custom_entry_price=lambda **k: 98.125,
        leverage=lambda **k: 1,
        custom_stake_amount=lambda **k: k["proposed_stake"],
        confirm_trade_entry=lambda **k: True,
        max_open_trades=1,
        stoploss=-0.1,
    )
    e.order_id_counter = e.trade_id_counter = 0
    e.fee, e.timeframe_min, e._exchange_name = 0.0, 5, "binance"
    e.canceled_trade_entries = e.canceled_entry_orders = e.canceled_exit_orders = 0
    e.setup_dated_price_rules(book or rules())
    return e


def row(at):
    return (pd.Timestamp(at), 100.0, 101.0, 99.0, 100.0, 1, 0, 0, 0, None, None)


def test_native_entry_and_requested_replacement_use_effective_tick():
    e = engine()
    before = e._enter_trade(PAIR, row(AT - timedelta(minutes=5)), "long")
    assert before.orders[-1].price == 98.13
    after = e._enter_trade(PAIR, row(AT), "long", requested_rate=98.125)
    assert after.orders[-1].price == 98.125
    assert after.orders[-1].ft_is_open
    assert before.orders[-1].price == 98.13


def test_native_exit_uses_new_rule_without_changing_trade_basis_or_old_order():
    e = engine()
    trade = e._enter_trade(PAIR, row(AT - timedelta(minutes=5)), "long")
    old = trade.orders[-1]
    old.ft_is_open = False
    trade.amount, trade.stake_amount = 1.0, trade.open_rate
    saved = (trade.price_precision, trade.precision_mode_price, trade.open_rate)
    e._exit_trade(trade, row(AT), 102.125, 1.0, "fixture")
    assert trade.orders[-1].price == 102.125
    assert old.price == 98.13
    assert (trade.price_precision, trade.precision_mode_price, trade.open_rate) == saved
    assert trade.orders[-1]._trade_bt is trade


def test_existing_pending_order_keeps_original_price_on_native_match_check():
    e = engine()
    trade = e._enter_trade(PAIR, row(AT - timedelta(minutes=5)), "long")
    order = trade.orders[-1]
    assert not e._try_close_open_order(order, trade, AT, row(AT))
    assert order.price == order.ft_price == 98.13
    assert order.ft_is_open


def test_native_entry_equal_to_candle_open_still_normalizes_before_sizing():
    e = engine()
    candle = list(row(AT - timedelta(minutes=5)))
    candle[1], candle[3] = 98.125, 97.0
    seen = []
    e.exchange.get_min_pair_stake_amount = lambda pair, rate, *a, **k: seen.append(rate) or 0
    trade = e._enter_trade(PAIR, tuple(candle), "long", requested_rate=96.125)
    # The native custom-entry shortcut (same as open) and requested-rate bypass
    # both reach the new local seam. The requested order remains outside candle.
    assert seen == [98.13]
    assert trade.orders[-1].price == 96.13


def test_native_old_pending_fill_survives_a_coarser_new_grid():
    book = rules()
    coarse = replace(
        book,
        rules=(
            replace(book.rules[0], tick=Decimal("0.001")),
            replace(book.rules[1], tick=Decimal("0.01")),
        ),
    )
    e = engine(coarse)
    trade = e._enter_trade(PAIR, row(AT - timedelta(minutes=5)), "long")
    trade.orders[-1].ft_is_open = False
    trade.amount = 1.0
    e._exit_trade(trade, row(AT - timedelta(minutes=5)), 102.125, 1.0, "fixture")
    order = trade.orders[-1]
    assert order.price == 102.125
    # This focused price test stubs fee accrual, not matching or order closure.
    e._run_funding_fees = lambda *a, **k: None
    e.strategy.order_filled = lambda **k: None
    fill_row = (pd.Timestamp(AT), 102.1, 102.2, 102.0, 102.1)
    assert e._try_close_open_order(order, trade, AT, fill_row)
    assert not order.ft_is_open and order.safe_price == 102.125
    assert order.order_filled_date == AT


def test_unknown_or_late_known_rule_never_falls_back_to_snapshot():
    book = rules()
    for pair, at in ((PAIR, START - timedelta(seconds=1)), (PAIR, END), ("OTHER", AT)):
        with pytest.raises(ValueError, match="Unknown"):
            book.rule_at(pair, at)
    late = replace(book.rules[1], known_at=AT + timedelta(minutes=1))
    with pytest.raises(ValueError, match="Unknown"):
        replace(book, rules=(book.rules[0], late)).rule_at(PAIR, AT)
    with pytest.raises(ValueError, match="aware"):
        book.rule_at(PAIR, AT.replace(tzinfo=None))


def test_future_rule_append_cannot_change_earlier_prefix():
    book = rules()
    prefix = replace(book, rules=(book.rules[0],))
    for delta in (0, 1, 60, 3600):
        at = START + timedelta(seconds=delta)
        assert prefix.rule_at(PAIR, at) == book.rule_at(PAIR, at)
    with pytest.raises(FrozenInstanceError):
        book.rules[0].tick = Decimal("0.1")
    with pytest.raises(ValueError, match="Overlapping"):
        replace(book, rules=(*book.rules, replace(book.rules[1], tick=Decimal("0.1"))))


def test_complete_window_checks_all_intervals_not_just_recorded_trade_times():
    book = rules()
    book.validate_window(START, END)
    gap = replace(book.rules[1], valid_from=AT + timedelta(seconds=1))
    late = replace(book.rules[1], known_at=AT + timedelta(seconds=1))
    for modified in (
        replace(book, rules=(book.rules[0],)),
        replace(book, rules=(book.rules[0], gap)),
        replace(book, rules=(book.rules[0], late)),
    ):
        with pytest.raises(ValueError, match="Incomplete"):
            modified.validate_window(START, END)
    suspended = replace(book.rules[1], suspended=True)
    with pytest.raises(ValueError, match="suspension integration"):
        replace(book, rules=(book.rules[0], suspended)).validate_window(START, END)


def test_suspension_aborts_native_entry_exit_match_cancel_paths():
    book = rules()
    suspended = replace(book, rules=(book.rules[0], replace(book.rules[1], suspended=True)))
    e = engine(suspended)
    trade = e._enter_trade(PAIR, row(AT - timedelta(minutes=5)), "long")
    pending, n = trade.orders[-1], len(trade.orders)
    for action in (
        lambda: e._enter_trade(PAIR, row(AT), "long"),
        lambda: e._exit_trade(trade, row(AT), 102.125, 1.0, "fixture"),
        lambda: e._try_close_open_order(pending, trade, AT, row(AT)),
        lambda: e.cancel_open_orders(trade, AT),
        lambda: e.check_order_cancel(trade, pending, AT),
    ):
        with pytest.raises(ValueError, match="suspended"):
            action()
    assert len(trade.orders) == n and pending.ft_is_open


@pytest.mark.parametrize("fault", ["live", "db", "pool", "mode", "book", "flag"])
def test_contract_drift_rejects(fault):
    e = engine()
    if fault == "live":
        e.config["runmode"] = RunMode.LIVE
    elif fault == "db":
        Trade.use_db = True
    elif fault == "pool":
        e.config["exchange"]["pair_whitelist"] = ["OTHER"]
    elif fault == "mode":
        e.trading_mode = TradingMode.SPOT
    elif fault == "book":
        e._dated_price_book = rules()
    else:
        e.config["research_dated_price_rules"] = False
    with pytest.raises(ValueError, match="contract"):
        e.get_pair_precision(PAIR, AT)


def test_native_defaults_preserve_upstream_price_choices():
    plain = Backtesting.__new__(Backtesting)
    assert plain.normalize_entry_order_rate(PAIR, AT, 98.125, "limit") == 98.125
    trade = SimpleNamespace(price_precision=0.01, precision_mode_price=4)
    assert plain.get_exit_order_precision(trade, AT) == (0.01, 4)


@pytest.mark.parametrize("requested", [None, 96.125])
def test_default_native_orders_match_sealed_prechange_engine(requested):
    root = Path(__file__).resolve().parents[3]
    source = root / (
        "research/equity/split_trade_native_20261116_v1/final_source_snapshot/"
        "freqtrade/optimize/backtesting.py"
    )
    spec = spec_from_file_location("pre_dated_precision_engine", source)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    observed = []
    for cls in (module.Backtesting, Backtesting):
        seed = engine()
        native = cls.__new__(cls)
        native.__dict__.update(seed.__dict__)
        native.price_pair_prec = {}
        native.exchange.get_precision_price = lambda pair: 0.01
        trade = native._enter_trade(PAIR, row(AT), "long", requested_rate=requested)
        trade.orders[-1].ft_is_open = False
        trade.amount = 1.0
        native._exit_trade(trade, row(AT), 102.125, 1.0, "fixture")
        observed.append([(o.price, o.amount, o.side) for o in trade.orders])
        LocalTrade.reset_trades()
    assert observed[0] == observed[1]


@pytest.mark.parametrize("tick", [Decimal(0), Decimal("NaN"), Decimal("1e-400")])
def test_invalid_tick_cannot_reach_native_float(tick):
    with pytest.raises(ValueError):
        replace(rules().rules[0], tick=tick)
