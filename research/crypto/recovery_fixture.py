"""Non-economic native persistence fixture, isolated from all forward paper ledgers."""

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from freqtrade.enums import CandleType, RunMode, TradingMode
from freqtrade.persistence import Order, Trade, init_db
from freqtrade.research.data import read, save, sha
from freqtrade.resolvers import StrategyResolver


def snapshot(trade, strategy, component):
    result = {
        key: getattr(trade, key)
        for key in (
            "id",
            "pair",
            "strategy",
            "enter_tag",
            "amount",
            "open_rate",
            "stake_amount",
            "max_stake_amount",
            "realized_profit",
            "funding_fees",
            "fee_open",
            "fee_close",
        )
    }
    result.update(
        open_date=trade.open_date_utc.isoformat(),
        entries=trade.nr_of_successful_entries,
        exits=trade.nr_of_successful_exits,
        orders=[
            {
                "id": o.id,
                "order_id": o.order_id,
                "side": o.ft_order_side,
                "amount": o.amount,
                "filled": o.filled,
                "price": o.safe_price,
                "funding_fee": o.funding_fee,
            }
            for o in trade.orders
        ],
    )
    if component == "nfi":
        result["profit_target_cache"] = strategy.target_profit_cache.data
    else:
        result["entry_atr"] = trade.get_custom_data(key="entry_atr")
        distance = strategy.custom_stoploss(
            trade.pair, trade, trade.open_date_utc, 100.0, 0.0, False
        )
        result["fixed_stop_price_at_probe100"] = 100 * (1 - distance)
    return result


def source_settings(package, component):
    parent = package / component
    manifest = read(parent / "package.json")
    settings = read(parent / "settings.json")
    if not settings["dry_run"] or any(settings["exchange"].get(k) for k in ("key", "secret")):
        raise ValueError("Fixture only accepts a credential-free paper source")
    for name, expected in manifest["strategy_files"].items():
        if sha(parent / "strategies" / name) != expected:
            raise ValueError("Fixture strategy source changed")
    return settings, parent


def run(package, output, component, mode):
    if output.is_relative_to(package) or package.is_relative_to(output):
        raise ValueError("Fixture state must be outside the forward paper package")
    settings, parent = source_settings(package, component)
    folder = output / component
    if mode == "write":
        folder.mkdir(parents=True, exist_ok=False)
    else:
        if not (folder / "expected.json").exists():
            raise ValueError("No fixture writer evidence")
    settings.update(
        user_data_dir=folder,
        strategy_path=str(parent / "strategies"),
        bot_name="fixture_" + component,
        runmode=RunMode.DRY_RUN,
        candle_type_def=CandleType.FUTURES,
    )
    database = folder / "state.sqlite"
    init_db("sqlite:///" + str(database))
    strategy = StrategyResolver.load_strategy(settings)
    pair = "ETH/USDT:USDT"
    opened = datetime(2026, 1, 1, 2, tzinfo=UTC)
    if mode == "write":
        trade = Trade(
            exchange="binance",
            pair=pair,
            base_currency="ETH",
            stake_currency="USDT",
            is_open=True,
            fee_open=0.0008,
            fee_close=0.0008,
            open_rate=100.0,
            stake_amount=100.0,
            amount=1.0,
            open_date=opened,
            leverage=1.0,
            is_short=False,
            trading_mode=TradingMode.FUTURES,
            funding_fees=0.0,
            strategy=settings["strategy"],
            enter_tag="61" if component == "nfi" else "daily_channel_20_10",
            amount_precision=3,
            price_precision=2,
            precision_mode=2,
            precision_mode_price=2,
            contract_size=1.0,
        )
        Trade.session.add(trade)
        Trade.commit()
        fills = [("buy", 1.0, 100.0)]
        if component == "nfi":
            fills += [("buy", 1.0, 80.0), ("sell", 0.5, 90.0)]
        for index, (side, amount, price) in enumerate(fills):
            order = Order(
                ft_order_side=side,
                ft_pair=pair,
                ft_is_open=False,
                ft_amount=amount,
                ft_price=price,
                order_id=f"dry_run_fixture_{index}",
                status="closed",
                symbol=pair,
                order_type="limit",
                side=side,
                price=price,
                average=price,
                amount=amount,
                filled=amount,
                remaining=0,
                cost=amount * price,
                order_date=opened + timedelta(seconds=index),
                order_filled_date=opened + timedelta(seconds=index),
                funding_fee=0.0,
            )
            trade.orders.append(order)
        trade.recalc_trade_from_orders()
        Trade.commit()
        if component == "nfi":
            strategy._set_profit_target(pair, "fixture_profit", 101.0, 0.1, opened)
        else:
            frame = pd.DataFrame(
                {"date": [pd.Timestamp(opened) - pd.Timedelta(hours=1)], "atr_1d": [5.0]}
            )
            strategy.dp = SimpleNamespace(get_analyzed_dataframe=lambda *a: (frame, None))
            strategy.order_filled(pair, trade, trade.orders[0], opened)
        Trade.commit()
        save(folder / "expected.json", snapshot(trade, strategy, component))
    else:
        trades = Trade.get_trades_proxy(is_open=True)
        if len(trades) != 1:
            raise ValueError("Fixture did not recover exactly one native open trade")
        # Trend recovery must use persisted ATR, with no DataProvider to recompute it.
        strategy.dp = None
        actual = snapshot(trades[0], strategy, component)
        if actual != read(folder / "expected.json"):
            save(folder / "mismatch.json", actual)
            raise ValueError(
                "Native quantities, orders or strategy state did not survive process restart"
            )
        save(
            folder / "recovered.json",
            {
                "passed": True,
                "state": actual,
                "purpose": "non-economic synthetic state fixture",
                "broker_loop_run": False,
                "real_orders_enabled": False,
            },
        )
    print({"component": component, "mode": mode, "fixture_database": str(database)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--component", choices=["nfi", "trend"], required=True)
    parser.add_argument("--mode", choices=["write", "read"], required=True)
    args = parser.parse_args()
    run(args.package.resolve(), args.output.resolve(), args.component, args.mode)
