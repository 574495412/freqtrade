"""Native pre-fill admission, not synthetic financial strategy performance."""

import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent / "settled_cash_native_20261114_v1"))
from test_settlement import account, at, config, contract, fill, trade, wallet

# Legacy fixture's bootstrap loads native classes first. Add only our new owners.
import importlib.util

ROOT = Path(__file__).resolve().parents[3]
for name, relative in [
    ("freqtrade.data.equity_execution", "freqtrade/data/equity_execution.py"),
    ("freqtrade.optimize.equity_execution", "freqtrade/optimize/equity_execution.py"),
]:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

from freqtrade.data.equity_execution import (
    EquityExecutionContract,
    SecurityExecutionRule,
    SpreadBand,
    SpreadTable,
    hkex_equity_spreads,
)
from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.enums import TradingMode
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.equity_backtesting import EquityBacktesting
from freqtrade.optimize.equity_execution import EquityFillValidationMixin
from freqtrade.persistence import LocalTrade, Order, Trade


class Probe(EquityFillValidationMixin, Backtesting):
    pass


def rules(c=None, lot=1, pair="ABC/USD"):
    c = c or contract()
    table = SpreadTable(Decimal(".01"), (SpreadBand(Decimal("10000"), Decimal(".01")),))
    r = SecurityExecutionRule(pair, c.effective_start, c.effective_end, lot, table, "c" * 64)
    return EquityExecutionContract(c, (r,))


def probe(c=None, r=None):
    c = c or contract()
    w = wallet(c)
    a = account(w)
    a.__class__ = Probe
    a.equity_execution_contract = r or rules(c)
    return a


def order(t, qty=5, price=100, entry=True, when=None):
    o = Order(
        order_id=f"{t.id}-{len(t.orders)}",
        ft_pair=t.pair,
        ft_order_side="buy" if entry else "sell",
        order_type="market",
        side="buy" if entry else "sell",
        price=price,
        ft_price=price,
        average=price,
        amount=qty,
        ft_amount=qty,
        filled=0.0,
        remaining=qty,
        status="open",
        ft_is_open=True,
        order_date=when or at(2),
    )
    o._trade_bt = t
    t.orders.append(o)
    return o


@pytest.fixture(autouse=True)
def native_state():
    previous = Trade.use_db
    Trade.use_db = False
    LocalTrade.reset_trades()
    with patch.object(Trade, "commit"):
        yield
    LocalTrade.reset_trades()
    Trade.use_db = previous


@pytest.mark.parametrize(
    "day,price,tick",
    [
        ("2025-08-01", "10", ".01"),
        ("2025-08-01", "10.02", ".02"),
        ("2025-08-01", "20.05", ".05"),
        ("2025-08-04", "10.01", ".01"),
        ("2025-08-04", "20.02", ".02"),
        ("2026-07-31", "9.99", ".01"),
        ("2026-08-03", "9.995", ".005"),
        ("2026-08-03", ".25", ".001"),
        ("2026-08-03", ".255", ".005"),
        ("2026-08-03", "9995", "5"),
    ],
)
def test_historical_hk_regimes(day, price, tick):
    table = hkex_equity_spreads(date.fromisoformat(day))
    assert table.tick_for(price) == Decimal(tick)
    table.require_price(price)


@pytest.mark.parametrize(
    "day,price",
    [
        ("2025-08-01", "10.01"),
        ("2025-08-01", "20.02"),
        ("2026-07-31", "9.995"),
        ("2026-08-03", ".2501"),
        ("2026-08-03", ".009"),
        ("2026-08-03", "10000"),
    ],
)
def test_hk_off_grid_and_coverage_rejected(day, price):
    with pytest.raises(EquityAdmissionError):
        hkex_equity_spreads(date.fromisoformat(day)).require_price(price)


@pytest.mark.parametrize(
    "fault", ["unknown", "gap", "overlap", "currency", "etf", "fraction", "unbound"]
)
def test_security_contract_rejects_unknown_and_ambiguous_versions(fault):
    c, r = contract(), rules().rules[0]
    with pytest.raises(EquityAdmissionError):
        if fault == "unknown":
            rules().rule_for("OTHER/USD", at(2))
        elif fault == "gap":
            EquityExecutionContract(c, (replace(r, effective_end=date(2025, 1, 9)),))
        elif fault == "overlap":
            EquityExecutionContract(c, (r, replace(r, effective_start=date(2025, 1, 3))))
        else:
            changes = {
                "currency": dict(pair="ABC/HKD"),
                "etf": dict(security_type="etf"),
                "fraction": dict(board_lot=0.5),
                "unbound": dict(source_sha256=""),
            }
            EquityExecutionContract(c, (replace(r, **changes[fault]),))


def test_lot_rounding_is_native_truncation_never_minimum_upsizing():
    c = contract("XHKG")
    r = rules(c, 200, "ABC/HKD")
    when = c.sessions[0].opens
    assert r.round_quantity("ABC/HKD", when, 199.99) == 0
    assert r.round_quantity("ABC/HKD", when, 599.99) == 400
    assert r.round_quantity("ABC/HKD", when, 600) == 600
    with pytest.raises(EquityAdmissionError, match="Off-lot"):
        r.require_fill("ABC/HKD", when, 599, 100)


def test_historical_board_lot_version_changes_without_current_master():
    c = contract()
    base = rules(c).rules[0]
    earlier = replace(base, effective_end=date(2025, 1, 3), board_lot=100)
    later = replace(base, effective_start=date(2025, 1, 6), board_lot=200)
    r = EquityExecutionContract(c, (earlier, later))
    assert r.round_quantity(base.pair, at(3), 350) == 300
    assert r.round_quantity(base.pair, at(6), 350) == 200
    assert earlier.board_lot == 100


@pytest.mark.parametrize("when", [at(2, 0), at(4), at(2).replace(tzinfo=None)])
def test_session_clock_cannot_be_daily_label_or_non_session(when):
    with pytest.raises(EquityAdmissionError):
        rules().require_fill("ABC/USD", when, 1, 100)


def test_hk_lunch_and_settlement_tail_remain_enforced():
    c = contract("XHKG")
    r = rules(c, 200, "ABC/HKD")
    for when in (at(2, 4), at(10, 2)):
        with pytest.raises(EquityAdmissionError):
            r.require_fill("ABC/HKD", when, 200, 100)


@pytest.mark.parametrize("qty,price", [(1.5, 100), (1, 100.001), (0, 100), (1, float("nan"))])
def test_bad_fill_rejected_before_native_order_mutation(qty, price):
    a, t = probe(), trade()
    a.wallets.advance(at(2))
    o = order(t, qty, price)
    row = [at(2), 100, 101, 99, 100]
    # NaN would not naturally hit a candle; isolate pre-fill validation as a negative test.
    with patch.object(a, "_get_order_filled", return_value=True):
        with pytest.raises(EquityAdmissionError):
            a._try_close_open_order(o, t, at(2), row)
    assert o.ft_is_open and o.filled == 0 and o.status == "open"
    assert t.amount == 0 and a.wallets.get_total("USD") == 1000


def test_native_entry_and_partial_full_exit_keep_settled_cash():
    a, t = probe(), trade()
    fill(a, t, at(2), 5, 100)
    fill(a, t, at(3), 2, 110, False)
    assert t.amount == 3
    assert a.wallets.cash_restriction.unsettled_sale_proceeds == pytest.approx(217.8)
    fill(a, t, at(6), 3, 90, False)
    assert not t.is_open
    assert a.wallets.get_total("USD") == pytest.approx(980.1)
    a.wallets.advance(at(7, 22))
    assert a.wallets.get_free("USD") == pytest.approx(980.1)


def test_cash_overspend_rejected_before_order_fill():
    a, t = probe(), trade()
    a.wallets.advance(at(2))
    o = order(t, 10, 100)
    with pytest.raises(EquityAdmissionError, match="settled cash"):
        a._try_close_open_order(o, t, at(2), [at(2), 100, 101, 99, 100])
    assert not o.filled and o.ft_is_open and t.amount == 0


@pytest.mark.parametrize(
    "fault", ["live", "db", "clock", "short", "pair", "side", "future", "oversell", "fee"]
)
def test_guard_rejects_unsupported_context_before_fill(fault):
    a, t = probe(), trade()
    a.wallets.advance(at(2))
    o = order(t)
    if fault == "live":
        a.config["dry_run"] = False
    elif fault == "db":
        Trade.use_db = True
    elif fault == "clock":
        a.wallets.asof -= timedelta(seconds=1)
    elif fault == "short":
        t.is_short = True
    elif fault == "pair":
        o.ft_pair = "OTHER/USD"
    elif fault == "side":
        o.side = "sell"
    elif fault == "future":
        o.order_date = at(3)
    elif fault == "oversell":
        o.side = o.ft_order_side = "sell"
    elif fault == "fee":
        t.fee_open = 0.02
    with pytest.raises(EquityAdmissionError):
        a._try_close_open_order(o, t, at(2), [at(2), 100, 101, 99, 100])
    assert not o.filled and o.ft_is_open


def test_unmatched_limit_order_remains_native_pending():
    a, t = probe(), trade()
    o = order(t, 1, 200)
    assert not a._try_close_open_order(o, t, at(2), [at(2), 100, 101, 99, 100])
    assert o.ft_is_open and not o.filled


def test_full_account_remains_closed():
    with patch.object(Backtesting, "__init__") as native:
        with pytest.raises(EquityAdmissionError, match="not integrated"):
            EquityBacktesting(config())
        native.assert_not_called()
