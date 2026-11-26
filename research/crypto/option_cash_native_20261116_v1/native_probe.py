"""Native constructor and actual native entry/exit objects on synthetic marks.

FUTURES is the native Trade/Order container only. The local mixin suppresses
perpetual funding/liquidation arithmetic;no venue market or account is certified.
"""

import argparse
import hashlib
import json
import socket
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
from bootstrap import HERE, ROOT, boot


boot()
from freqtrade.commands.optimize_commands import setup_optimize_configuration
from freqtrade.data.inverse_option_contracts import parse_instrument_response
from freqtrade.data.option_cash import OptionCashContract, OptionMark
from freqtrade.enums import ExitType, RunMode
from freqtrade.optimize.backtesting import HEADERS, Backtesting
from freqtrade.optimize.option_fee_execution import OptionFeeFillMixin, native_option_notional
from freqtrade.option_wallets import OptionPremiumWallets
from freqtrade.persistence import LocalTrade, Trade
from freqtrade.resolvers import ExchangeResolver


D = Decimal
START = datetime(2026, 11, 15, 21, tzinfo=UTC)
END = START + timedelta(hours=7)


class PremiumProbe(OptionFeeFillMixin, Backtesting):
    def get_pair_precision(self, pair, current_time):
        # Explicit fixed fixture tick. Metadata validates every actual fill;
        # no empirically learned future-price precision is used.
        return 0.0005, 4


def encode(value):
    if isinstance(value, (datetime, Decimal)):
        return str(value)
    if isinstance(value, timedelta):
        return value.total_seconds()
    raise TypeError(type(value).__name__)


def save(path, value):
    path.write_text(json.dumps(value, default=encode, indent=2, allow_nan=False) + "\n")


def specs(currency):
    old = HERE.parent / "option_premium_admission_native_20261116_v1"
    receipts = json.loads((old / "source_receipts.json").read_text())
    row = next(
        r
        for r in receipts
        if r.get("path", "").endswith("/" + currency.lower() + "_instruments.json")
    )
    raw = Path(row["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == row["sha256"]
    all_specs = parse_instrument_response(
        raw,
        source_url=row["url"],
        http_status=row["status"],
        observed_at=datetime.fromisoformat(row["observed_at"]),
    )
    call = next(s for s in all_specs if s.option_type == "call" and s.expiration_at > END)
    put = next(
        s
        for s in all_specs
        if s.option_type == "put"
        and s.expiration_at == call.expiration_at
        and s.strike_usd_per_base == call.strike_usd_per_base
    )
    return call, put


def marks(contract, at, price=".05"):
    digest = hashlib.sha256((HERE / "protocol.json").read_bytes()).hexdigest()
    return tuple(
        OptionMark(s.storage_pair, at, at, D(price), s.strike_usd_per_base, digest)
        for s in contract.specs
    )


def row(at, price):
    data = {
        "date": pd.Timestamp(at),
        "open": float(price),
        "high": float(price),
        "low": float(price),
        "close": float(price),
        "enter_long": 0,
        "exit_long": 0,
        "enter_short": 0,
        "exit_short": 0,
        "enter_tag": None,
        "exit_tag": None,
    }
    return tuple(data.get(key) for key in HEADERS)


def order_snapshot():
    return [
        {
            "trade_id": t.id,
            "pair": t.pair,
            "is_short": t.is_short,
            "order_id": o.order_id,
            "at": str(o.order_filled_utc),
            "created_at": str(o.order_date_utc),
            "side": o.ft_order_side,
            "quantity": str(o.safe_filled),
            "requested_quantity": str(o.safe_amount),
            "price": str(o.safe_price),
            "status": o.status,
            "ft_is_open": o.ft_is_open,
        }
        for t in Trade.get_trades_proxy()
        for o in t.orders
    ]


def build(currency, case, folder, *, scheduled=False, account_type=PremiumProbe):
    selected = specs(currency)
    minimum = selected[0].min_amount_base
    initial = (
        D(1) if case in {"short", "pending"} else minimum * (D(".1") if case == "long" else D(".3"))
    )
    digest = hashlib.sha256((HERE / "protocol.json").read_bytes()).hexdigest()
    contract = OptionCashContract(currency, selected, START, END, D(".0003"), D(".125"), digest)
    template = ROOT / "user_data/crypto_quant/copula_fills_native_20261115_v4/fixtures/normal1000"
    raw = json.loads((template / "config.json").read_text())
    raw.update(
        strategy="PremiumFixture",
        strategy_path=str(HERE),
        timeframe="1h",
        timeframe_detail="",
        dry_run_wallet=float(initial),
        stake_amount=float(minimum * D(".05")),
        stake_currency=currency,
        max_open_trades=4,
        tradable_balance_ratio=1.0,
        fee=0.0,
        trading_mode="futures",
        margin_mode="isolated",
        position_adjustment_enable=True,
        option_wallet_component=True,
        synthetic_option_fixture=True,
        user_data_dir=str(folder),
        datadir=str(folder),
        db_url="sqlite://",
    )
    raw["exchange"]["pair_whitelist"] = [s.storage_pair for s in selected]
    if scheduled:
        raw.update(
            synthetic_option_scheduled_start=True,
            fixture_entry_signal=(START - timedelta(hours=1)).isoformat(),
            fixture_exit_signal=(END - timedelta(hours=1)).isoformat(),
            position_adjustment_enable=False,
            export="trades",
        )
    raw.pop("timerange", None)
    folder.mkdir(parents=True, exist_ok=False)
    save(folder / "config.json", raw)
    config = setup_optimize_configuration(
        {"config": [str(folder / "config.json")], "datadir": str(folder)}, RunMode.BACKTEST
    )
    exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
    original = json.loads((template / "synthetic_markets_maintenance_fixed.json").read_text())
    seed = next(iter(original["markets"].values()))
    markets, tiers = {}, {}
    for s in selected:
        market = deepcopy(seed)
        market.update(
            id=s.instrument_name,
            symbol=s.storage_pair,
            base=s.instrument_name,
            quote=currency,
            settle=currency,
            baseId=s.instrument_name,
            quoteId=currency,
            settleId=currency,
            type="swap",
            spot=False,
            swap=True,
            future=False,
            contract=True,
            linear=True,
            inverse=False,
            contractSize=1.0,
            active=True,
        )
        market["precision"] = {"amount": float(minimum), "price": 0.0005}
        market["limits"] = {
            "amount": {"min": float(minimum), "max": 1e6},
            "price": {"min": 0.0001, "max": 1e6},
            "cost": {"min": 0.0000001, "max": 1e6},
            "leverage": {"min": 1.0, "max": 1.0},
        }
        markets[s.storage_pair] = market
        tiers[s.storage_pair] = [
            {
                "minNotional": 0.0,
                "maxNotional": 1e9,
                "maintenanceMarginRate": 0.005,
                "maintAmt": 0.0,
                "maxLeverage": 1.0,
            }
        ]
    currencies = {
        c: {"id": c, "code": c, "precision": 8}
        for c in [currency] + [s.instrument_name for s in selected]
    }
    exchange._markets, exchange._leverage_tiers = markets, tiers
    exchange._api.set_markets(markets, currencies)
    exchange._api_async.set_markets(markets, currencies)
    exchange.validate_config(config)
    account = account_type(config, exchange=exchange)
    account.wallets = OptionPremiumWallets(
        config, exchange, contract=contract, asof=START, marks=marks(contract, START)
    )
    account._set_strategy(account.strategylist[0])
    save(folder / "synthetic_carrier_markets.json", markets)
    save(folder / "option_contract.json", asdict(contract))
    return account, exchange, minimum


def execute(currency, case, folder):
    old = Trade.use_db
    Trade.use_db = False
    LocalTrade.reset_trades()
    account = exchange = None
    try:
        account, exchange, minimum = build(currency, case, folder)
        w, observations = account.wallets, []
        call, put = w.contract.specs
        input_marks = marks(w.contract, START)

        def snapshot(name):
            w.update()
            observations.append(
                {
                    "name": name,
                    "state": asdict(w.cash_state),
                    "input_marks": [asdict(m) for m in input_marks],
                    "native_orders": order_snapshot(),
                }
            )

        def advance(hour, price=".05"):
            nonlocal input_marks
            at = START + timedelta(hours=hour)
            supplied = marks(w.contract, at, price)
            w.advance(at, supplied)
            input_marks = supplied

        def enter(s, side, qty, price=".05", **kwargs):
            native_stake = native_option_notional(
                qty, D(price), exchange.markets[s.storage_pair], account.precision_mode
            )
            trade = account._enter_trade(
                s.storage_pair,
                row(w.asof, D(".05")),
                side,
                requested_rate=float(D(price)),
                requested_stake=native_stake,
                **kwargs,
            )
            w.update()
            assert trade is not None and D(str(trade.orders[-1].safe_amount)) == qty
            return trade

        def exit_(trade, qty, price):
            r = row(w.asof, D(price))
            closed = account._exit_trade(
                trade, r, float(D(price)), float(qty), ExitType.EXIT_SIGNAL.value
            )
            account._process_exit_order(closed.orders[-1], trade, w.asof, r, trade.pair)
            w.update()

        snapshot("initial")
        if case == "short":
            a = enter(call, "short", 2 * minimum)
            b = enter(put, "short", 2 * minimum)
            snapshot("short_premiums_received")
            advance(1, ".10")
            snapshot("short_mark_loss")
            advance(2, ".08")
            exit_(a, minimum, ".08")
            snapshot("partial_call_close")
            advance(3, ".06")
            exit_(a, minimum, ".06")
            snapshot("call_flat")
            advance(4, ".04")
            exit_(b, 2 * minimum, ".04")
            snapshot("all_flat")
        elif case == "long":
            a = enter(call, "long", minimum)
            snapshot("long_premium_paid")
            advance(1, ".10")
            snapshot("long_mark_gain_not_cash")
            assert w.get_available_stake_amount() < float(minimum * D(".05"))
            advance(2, ".08")
            exit_(a, minimum, ".08")
            snapshot("long_sale_cash")
            advance(3)
            enter(put, "long", minimum)
            snapshot("realized_cash_reinvested")
        elif case == "breach":
            enter(call, "short", minimum)
            snapshot("short_before_shock")
            advance(1, ".50")
            snapshot("maintenance_breach")
            assert w.cash_state.liquidation_required and w.get_available_stake_amount() == 0
        else:
            a = enter(call, "short", minimum, ".15")
            snapshot("pending_short_without_premium_credit")
            assert not w.fill_journal
            account.cancel_open_orders(a, w.asof)
            w.update()
            snapshot("native_cancel_releases_reserve")
        orders = [
            {
                "trade_id": t.id,
                "pair": t.pair,
                "is_short": t.is_short,
                "order_id": o.order_id,
                "at": str(o.order_filled_utc),
                "side": o.ft_order_side,
                "quantity": str(o.safe_filled),
                "price": str(o.safe_price),
                "status": o.status,
                "ft_is_open": o.ft_is_open,
            }
            for t in Trade.get_trades_proxy()
            for o in t.orders
        ]
        result = {
            "currency": currency,
            "case": case,
            "native_constructor": True,
            "native_order_matching": True,
            "native_backtest_start": False,
            "synthetic_only": True,
            "orders": orders,
            "snapshots": observations,
            "trade_count": len(Trade.get_trades_proxy()),
            "actual_fill_count": len(w.fill_journal),
            "historical_option_accounts": 0,
            "factor_analysis": 0,
            "bench": 0,
            "strategy_net_return": None,
            "liquidation_executed": False,
        }
        save(folder / "native_trace.json", result)
        print(
            currency,
            case,
            "native fills",
            len(w.fill_journal),
            "snapshots",
            len(observations),
            flush=True,
        )
        return result
    except Exception:
        if account is not None:
            save(
                folder / "native_failure.json",
                {
                    "snapshots": observations if "observations" in locals() else [],
                    "state": asdict(account.wallets.cash_state),
                    "trades": [
                        {
                            "id": t.id,
                            "pair": t.pair,
                            "is_short": t.is_short,
                            "amount": t.amount,
                            "is_open": t.is_open,
                            "orders": [
                                {
                                    "id": o.order_id,
                                    "side": o.ft_order_side,
                                    "amount": o.safe_amount,
                                    "filled": o.safe_filled,
                                    "price": o.safe_price,
                                    "is_open": o.ft_is_open,
                                }
                                for o in t.orders
                            ],
                        }
                        for t in Trade.get_trades_proxy()
                    ],
                },
            )
        raise
    finally:
        if account is not None:
            account.cleanup()
        if exchange is not None:
            exchange.close()
        LocalTrade.reset_trades()
        Trade.use_db = old


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--currency", choices=["BTC", "ETH"], required=True)
    parser.add_argument("--case", choices=["long", "short", "breach", "pending"], required=True)
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args()

    def offline(*args, **kwargs):
        raise RuntimeError("Synthetic option component is offline")

    socket.socket.connect = offline
    execute(args.currency, args.case, HERE / "runs" / args.attempt)
