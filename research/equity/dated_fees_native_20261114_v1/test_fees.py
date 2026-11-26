"""Component tests with actual native fills; no historical strategy accounts."""

import sys
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent / "settled_cash_native_20261114_v1"))
from test_settlement import account, at, config, contract, exchange, fill, trade

from freqtrade.data.equity_execution import (
    EquityExecutionContract,
    SecurityExecutionRule,
    SpreadBand,
    SpreadTable,
)
from freqtrade.data.equity_fees import DatedFeeRule, EquityFeeContract
from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.equity_fee_wallets import EquityFeeWallets
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.equity_backtesting import EquityBacktesting
from freqtrade.optimize.equity_fee_execution import EquityFeeFillMixin
from freqtrade.persistence import LocalTrade, Order, Trade


class Probe(EquityFeeFillMixin, Backtesting):
    pass


def fees(c=None, pair="ABC/USD"):
    c = c or contract()
    rows = []
    for side in ("buy", "sell"):
        rows.append(
            DatedFeeRule(
                pair,
                "commission",
                side,
                date(2025, 1, 1),
                date(2025, 1, 31),
                "a" * 64,
                rate=D(".001"),
                minimum=D(1),
            )
        )
        rows.append(
            DatedFeeRule(
                pair,
                "sale_tax",
                side,
                date(2025, 1, 1),
                date(2025, 1, 31),
                "b" * 64,
                rate=D(".002") if side == "sell" else D(0),
                rounding="UP",
            )
        )
    return EquityFeeContract(c, (pair,), ("commission", "sale_tax"), tuple(rows))


def probe(c=None, fee_contract=None):
    c = c or contract()
    f = fee_contract or fees(c, "ABC/" + c.currency)
    w = EquityFeeWallets(config(c.currency), exchange(c.currency), fees=f, asof=c.sessions[0].opens)
    a = account(w)
    a.__class__ = Probe
    table = SpreadTable(D(".01"), (SpreadBand(D("10000"), D(".01")),))
    a.equity_execution_contract = EquityExecutionContract(
        c,
        (
            SecurityExecutionRule(
                f.pairs[0], c.effective_start, c.effective_end, 1, table, "c" * 64
            ),
        ),
    )
    return a


def zero_trade(pair="ABC/USD", when=None):
    t = trade(pair, when)
    t.fee_open = t.fee_close = 0.0
    return t


@pytest.fixture(autouse=True)
def isolated():
    old = Trade.use_db
    Trade.use_db = False
    LocalTrade.reset_trades()
    with patch.object(Trade, "commit"):
        yield
    LocalTrade.reset_trades()
    Trade.use_db = old


@pytest.mark.parametrize(
    "side,qty,price,want",
    [
        ("buy", 1, 1, "1.00"),
        ("sell", 1, 1, "1.01"),
        ("buy", 100, 100, "10.00"),
        ("sell", 100, 100, "30.00"),
        ("sell", 3, ".83", "1.01"),
    ],
)
def test_sides_minimum_and_exact_rounding(side, qty, price, want):
    assert fees().quote("ABC/USD", at(2), side, qty, price).total == D(want)


def test_component_cap_and_rounding_order():
    r = fees().rules[0]
    r = replace(r, rate=D(".00002"), minimum=D(2), maximum=D(100))
    assert [r.charge(1, p) for p in (100, 1000000, 10000000)] == [D(2), D(20), D(100)]
    assert replace(r, minimum=D(0), maximum=None, rate=D(".000042")).charge(1, 100) == D(0)
    assert replace(r, minimum=D(0), maximum=None, rate=D(".000042"), rounding="UP").charge(
        1, 100
    ) == D(".01")


@pytest.mark.parametrize(
    "fault",
    ["negative", "float", "nan", "cap", "quantum", "align", "date", "source", "side", "clock"],
)
def test_invalid_fee_rules_reject(fault):
    changes = {
        "negative": {"rate": D(-1)},
        "float": {"rate": 0.001},
        "nan": {"per_share": D("NaN")},
        "cap": {"minimum": D(2), "maximum": D(1)},
        "quantum": {"quantum": D(0)},
        "align": {"minimum": D(".001")},
        "date": {"end": date(2024, 1, 1)},
        "source": {"source_sha256": ""},
        "side": {"side": "short"},
        "clock": {"clock": "today"},
    }
    with pytest.raises(EquityAdmissionError):
        replace(fees().rules[0], **changes[fault])


@pytest.mark.parametrize(
    "fault",
    [
        "unknown",
        "gap",
        "overlap",
        "missing_side",
        "currency",
        "foreign_component",
        "mixed_clock",
        "aggregation",
    ],
)
def test_missing_and_ambiguous_contracts_reject(fault):
    f = fees()
    with pytest.raises(EquityAdmissionError):
        if fault == "unknown":
            f.quote("OTHER/USD", at(2), "buy", 1, 100)
        elif fault == "gap":
            replace(
                f, rules=tuple(replace(r, start=date(2025, 1, 3)) for r in f.rules)
            ).require_coverage()
        elif fault == "overlap":
            replace(f, rules=f.rules + (f.rules[0],))
        elif fault == "missing_side":
            replace(f, rules=f.rules[1:])
        elif fault == "currency":
            replace(f, pairs=("ABC/HKD",), rules=tuple(replace(r, pair="ABC/HKD") for r in f.rules))
        elif fault == "foreign_component":
            replace(f, rules=f.rules + (replace(f.rules[0], component="unlisted"),))
        elif fault == "mixed_clock":
            replace(
                f,
                rules=f.rules
                + (
                    replace(
                        f.rules[0],
                        start=date(2025, 2, 1),
                        end=date(2025, 2, 3),
                        clock="settlement_date",
                    ),
                ),
            )
        else:
            replace(f, charging_unit="daily_aggregate")


def test_settlement_clock_not_naive_trade_date_and_prefix_causality():
    f = fees()
    base = replace(
        f.rules[0], minimum=D(0), rate=D(".01"), clock="settlement_date", end=date(2025, 1, 3)
    )
    later = replace(base, start=date(2025, 1, 6), end=date(2025, 1, 31), rate=D(".02"))
    f = replace(f, rules=(base, later) + f.rules[1:])
    assert f.quote("ABC/USD", at(2), "buy", 1, 100).total == D(1)
    # Jan 3 fill settles Jan 6, after rate change, even though trade date precedes it.
    assert f.quote("ABC/USD", at(3), "buy", 1, 100).total == D(2)
    changed = replace(f, rules=(base, replace(later, rate=D(".9"))) + f.rules[2:])
    assert changed.quote("ABC/USD", at(2), "buy", 1, 100) == f.quote(
        "ABC/USD", at(2), "buy", 1, 100
    )
    truncated = replace(f, rules=(base,) + f.rules[2:])
    assert truncated.quote("ABC/USD", at(2), "buy", 1, 100) == f.quote(
        "ABC/USD", at(2), "buy", 1, 100
    )


def test_affordable_lots_matches_exhaustive_grid():
    f = fees()
    for lot in (1, 10, 200):
        for price in (D(".01"), D("1.23"), D(100)):
            for budget in (D(0), D(".50"), D(99), D(1000)):
                q = f.affordable_quantity("ABC/USD", at(2), price, lot, budget)
                if q:
                    assert q * price + f.quote("ABC/USD", at(2), "buy", q, price).total <= budget
                next_q = q + lot
                assert (
                    next_q * price + f.quote("ABC/USD", at(2), "buy", next_q, price).total > budget
                )
    assert f.affordable_quantity("ABC/USD", at(2), 100, 1, 1000) == 9


def test_actual_native_partial_full_exit_and_settlement_net_of_each_order_fee():
    a, t = probe(), zero_trade()
    fill(a, t, at(2), 5, 100)
    assert a.wallets.get_free("USD") == pytest.approx(499)
    fill(a, t, at(3), 2, 110, False)
    assert t.amount == 3
    assert a.wallets.cash_restriction.unsettled_sale_proceeds == pytest.approx(218.56)
    assert a.wallets.get_free("USD") == pytest.approx(499)
    fill(a, t, at(6), 3, 90, False)
    assert not t.is_open
    assert a.wallets.get_total("USD") == pytest.approx(986.02)
    assert a.wallets.total_execution_fees == D("3.98")
    assert LocalTrade.bt_total_profit == pytest.approx(-10)  # deliberately gross native trade PnL
    a.wallets.advance(at(6, 22))
    assert a.wallets.get_free("USD") == pytest.approx(717.56)
    a.wallets.advance(at(7, 22))
    for _ in range(3):
        a.wallets.update()
        assert a.wallets.get_free("USD") == pytest.approx(986.02)
        assert len(a.wallets.fee_journal) == 3


def test_actual_native_dca_and_partial_fees_never_scale_from_average_entry():
    a, t = probe(), zero_trade()
    fill(a, t, at(2), 2, 100)
    fill(a, t, at(3), 3, 110)
    fill(a, t, at(6), 2, 120, False)
    assert a.wallets.total_execution_fees == D("3.48")
    assert a.wallets.get_free("USD") == pytest.approx(468)
    assert a.wallets.cash_restriction.unsettled_sale_proceeds == pytest.approx(238.52)


def test_hk_tplus2_net_fee_claim_and_release_boundary():
    c = contract("XHKG")
    a, t = probe(c), zero_trade("ABC/HKD", at(2, 2))
    fill(a, t, at(2, 2), 5, 100)
    fill(a, t, at(3, 2), 5, 110, False)
    assert a.wallets.get_free("HKD") == pytest.approx(499)
    assert a.wallets.cash_restriction.unsettled_sale_proceeds == pytest.approx(547.9)
    a.wallets.advance(at(7, 10) - timedelta(microseconds=1))
    assert a.wallets.get_free("HKD") == pytest.approx(499)
    a.wallets.advance(at(7, 10))
    assert a.wallets.get_free("HKD") == pytest.approx(1046.9)


def pending_order(t, qty=10):
    o = Order(
        order_id="pending",
        ft_pair=t.pair,
        ft_order_side="buy",
        side="buy",
        order_type="market",
        price=100,
        ft_price=100,
        average=100,
        amount=qty,
        ft_amount=qty,
        filled=0.0,
        remaining=qty,
        status="open",
        ft_is_open=True,
        order_date=at(2),
    )
    t.orders.append(o)
    return o


@pytest.mark.parametrize(
    "fault", ["budget", "native_fee", "live", "wrong_clock", "off_lot", "missing_rules"]
)
def test_failures_reject_before_native_order_mutation(fault):
    a, t = probe(), zero_trade()
    a.wallets.advance(at(2))
    o = pending_order(t, 10 if fault == "budget" else 1)
    if fault == "native_fee":
        t.fee_close = 0.001
    elif fault == "live":
        a.config["runmode"] = "live"
    elif fault == "wrong_clock":
        a.wallets.asof -= timedelta(seconds=1)
    elif fault == "off_lot":
        o.amount = o.ft_amount = o.remaining = 0.5
    elif fault == "missing_rules":
        a.wallets.fees = replace(
            a.wallets.fees,
            rules=tuple(replace(r, start=date(2025, 1, 3)) for r in a.wallets.fees.rules),
        )
    with pytest.raises(EquityAdmissionError):
        a._try_close_open_order(o, t, at(2), [at(2), 100, 101, 99, 100])
    assert o.status == "open" and not o.filled and t.amount == 0
    assert a.wallets.total_execution_fees == 0


def test_full_account_entry_stays_closed():
    with patch.object(Backtesting, "__init__") as native:
        with pytest.raises(EquityAdmissionError, match="not integrated"):
            EquityBacktesting(config())
        native.assert_not_called()


def test_negative_net_sale_rejects_before_fill():
    a, t = probe(), zero_trade()
    fill(a, t, at(2), 5, 100)
    a.wallets.advance(at(3))
    o = pending_order(t, 1)
    o.ft_order_side = o.side = "sell"
    o.ft_price = o.price = o.average = 0.01
    with pytest.raises(EquityAdmissionError, match="Negative net sale"):
        a._try_close_open_order(o, t, at(3), [at(3), 0.01, 0.02, 0.01, 0.01])
    assert not o.filled and o.ft_is_open and t.amount == 5


def test_update_failure_rolls_back_wallet_and_fee_journal():
    a, t = probe(), zero_trade()
    fill(a, t, at(2), 5, 100)
    old = (
        a.wallets._wallets,
        a.wallets.cash_restriction,
        a.wallets.fee_journal,
        a.wallets.total_execution_fees,
    )
    t.fee_close = 0.001
    with pytest.raises(EquityAdmissionError, match="percentages must be zero"):
        a.wallets.update()
    assert old == (
        a.wallets._wallets,
        a.wallets.cash_restriction,
        a.wallets.fee_journal,
        a.wallets.total_execution_fees,
    )


def test_unmatched_order_has_no_fee_charge():
    a, t = probe(), zero_trade()
    a.wallets.advance(at(2))
    o = pending_order(t, 1)
    assert not a._try_close_open_order(o, t, at(2), [at(2), 90, 91, 89, 90])
    assert o.ft_is_open and not o.filled and a.wallets.total_execution_fees == 0


def test_zero_fee_native_wallet_parity_for_same_actual_fill_sequence():
    from freqtrade.equity_wallets import EquitySettlementWallets

    snapshots = []
    for use_overlay in (False, True):
        LocalTrade.reset_trades()
        c = contract()
        if use_overlay:
            f = fees(c)
            f = replace(f, rules=tuple(replace(r, rate=D(0), minimum=D(0)) for r in f.rules))
            a = probe(c, f)
        else:
            w = EquitySettlementWallets(
                config(), exchange(), contract=c, asof=c.sessions[0].opens, entry_fee_rate=0.0
            )
            a = account(w)
        t, rows = zero_trade(), []
        for when, qty, price, entry in [
            (at(2), 2, 100, True),
            (at(3), 3, 110, True),
            (at(6), 2, 120, False),
            (at(7), 3, 90, False),
        ]:
            fill(a, t, when, qty, price, entry)
            rows.append(
                (
                    a.wallets.get_free("USD"),
                    a.wallets.get_used("USD"),
                    a.wallets.get_total("USD"),
                    t.amount,
                    t.stake_amount,
                    LocalTrade.bt_total_profit,
                )
            )
        a.wallets.advance(at(8, 22))
        rows.append((a.wallets.get_free("USD"), a.wallets.get_total("USD")))
        snapshots.append(rows)
    assert snapshots[0] == snapshots[1]


def test_sizing_respects_native_capital_ratio_and_backtest_isolation():
    a = probe()
    a.wallets.advance(at(2))
    a.wallets._config["tradable_balance_ratio"] = .5
    assert a.wallets.get_available_stake_amount() == 500
    assert a.wallets.affordable_quantity("ABC/USD", at(2), 100, 1) == 4
    a.wallets._config["dry_run"] = False
    with pytest.raises(EquityAdmissionError, match="isolated BACKTEST"):
        a.wallets.affordable_quantity("ABC/USD", at(2), 100, 1)
