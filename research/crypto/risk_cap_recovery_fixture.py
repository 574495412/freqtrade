"""Two-process native risk-state recovery with synthetic fills outside every paper account."""

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from freqtrade.enums import CandleType, RunMode, TradingMode
from freqtrade.persistence import Order, Trade, init_db
from freqtrade.research.data import read, save, sha
from freqtrade.resolvers import StrategyResolver


KEY = "nfi_risk_cap_v1"


def snapshot(trade, strategy):
    clock = datetime(2026, 1, 1, tzinfo=UTC)
    # A deliberately unsafe legacy rebuy proposal after a reduction. Only fixture data.
    row = {
        "close": 80,
        "protections_long_global": True,
        "protections_long_rebuy": True,
        "close_max_12": 80,
        "close_max_24": 80,
        "close_max_48": 80,
        "btc_pct_close_max_72_5m": 0,
        "btc_pct_close_max_24_5m": 0,
        "rsi_3": 20,
        "rsi_3_15m": 20,
        "rsi_3_1h": 20,
        "rsi_3_4h": 20,
        "rsi_14": 30,
    }
    strategy.dp = SimpleNamespace(
        runmode=RunMode.DRY_RUN,
        get_analyzed_dataframe=lambda *a: (pd.DataFrame([row, row]), None),
        ticker=lambda *a: {"bid": 80, "ask": 80},
        send_msg=lambda *a: None,
    )
    args = (trade, clock, 80, -0.2, 1, 1000, 80, 80, -0.2, -0.2)
    legacy_proposal = strategy.long_rebuy_adjust_trade_position(*args)
    guarded_proposal = strategy.adjust_trade_position(*args)
    if legacy_proposal is None or legacy_proposal <= 0 or guarded_proposal is not None:
        raise ValueError(
            "The fixture must exercise a real legacy addition blocked by the risk guard"
        )
    distance = strategy.custom_stoploss(trade.pair, trade, clock, 100, 0, True)
    return {
        "trade_id": trade.id,
        "amount": trade.amount,
        "stake_amount": trade.stake_amount,
        "open_rate": trade.open_rate,
        "realized_profit": trade.realized_profit,
        "risk_state": trade.get_custom_data(KEY),
        "entries": trade.nr_of_successful_entries,
        "exits": trade.nr_of_successful_exits,
        "order_ids": [o.order_id for o in trade.orders],
        "stop_at_probe100": 100 * (1 - distance),
        "legacy_add_proposal_usdt": legacy_proposal,
        "guarded_add_proposal": guarded_proposal,
        "risk_exit_allowed": strategy.confirm_trade_exit(
            trade.pair, trade, "market", trade.amount, 80, "gtc", "stop_loss", clock
        ),
    }


def run(reference, output, mode):
    if output.is_relative_to(reference) or reference.is_relative_to(output):
        raise ValueError("Fixture must have independent state")
    plan = read(reference / "plan.json")
    for source in plan["strategy_sources"]:
        if sha(reference / "strategies" / Path(source["path"]).name) != source["sha256"]:
            raise ValueError("Native strategy snapshot changed")
    if mode == "write":
        output.mkdir(parents=True, exist_ok=False)
    config = read(reference / "settings.json")
    config.update(user_data_dir=output, runmode=RunMode.DRY_RUN, candle_type_def=CandleType.FUTURES)
    init_db("sqlite:///" + str(output / "fixture.sqlite"))
    strategy = StrategyResolver.load_strategy(config)
    if mode == "write":
        opened = datetime(2026, 1, 1, tzinfo=UTC)
        trade = Trade(
            exchange="binance",
            pair="ETH/USDT:USDT",
            base_currency="ETH",
            stake_currency="USDT",
            is_open=True,
            fee_open=0.0008,
            fee_close=0.0008,
            open_rate=100,
            amount=0.4,
            stake_amount=40,
            open_date=opened,
            leverage=1,
            is_short=False,
            trading_mode=TradingMode.FUTURES,
            funding_fees=0,
            strategy=config["strategy"],
            enter_tag="61",
            amount_precision=8,
            price_precision=2,
            precision_mode=2,
            precision_mode_price=2,
            contract_size=1,
            stop_loss=88,
            initial_stop_loss=88,
        )
        Trade.session.add(trade)
        Trade.commit()
        for index, (side, amount, price) in enumerate(
            [("buy", 0.4, 100), ("buy", 0.4, 96), ("sell", 0.2, 105)]
        ):
            order = Order(
                ft_order_side=side,
                ft_pair=trade.pair,
                ft_is_open=False,
                ft_amount=amount,
                ft_price=price,
                order_id=f"dry_run_risk_fixture_{index}",
                status="closed",
                symbol=trade.pair,
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
                funding_fee=0,
            )
            trade.orders.append(order)
            trade.recalc_trade_from_orders()
            strategy.order_filled(trade.pair, trade, order, opened)
            Trade.commit()
        save(output / "expected.json", snapshot(trade, strategy))
    else:
        trades = Trade.get_trades_proxy(is_open=True)
        if len(trades) != 1:
            raise ValueError("Exactly one synthetic open position must recover")
        actual = snapshot(trades[0], strategy)
        if actual != read(output / "expected.json"):
            save(output / "mismatch.json", actual)
            raise ValueError("Persisted risk state or native callbacks differ after restart")
        save(
            output / "recovered.json",
            {
                "passed": True,
                "state": actual,
                "scope": "Synthetic native DB and callbacks; no broker loop or profitability proof",
                "real_orders_enabled": False,
            },
        )
    print({"mode": mode, "database": str(output / "fixture.sqlite")})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mode", choices=["write", "read"], required=True)
    args = parser.parse_args()
    run(args.reference.resolve(), args.output.resolve(), args.mode)
