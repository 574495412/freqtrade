"""Check the combined adapter against archived native adjustment requests."""

import json
import shutil
import subprocess
import sys
from collections import Counter
from math import fsum, isclose
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .annual_trade_scale import FIVE_MINUTES
from .inputs import HERE
from .volatility_across_years import selected_receipt


def order_state(data, request, clock):
    from freqtrade.enums import TradingMode
    from freqtrade.persistence import LocalTrade, Order

    time_ms = clock.value // 1_000_000
    active, = (trade for trade in data if trade["pair"] == request["pair"]
               and trade["enter_tag"] == request["entry_tag"]
               and trade["open_timestamp"] <= time_ms <= trade["close_timestamp"])
    prefix = [order for order in active["orders"] if order["order_filled_timestamp"] < time_ms]
    first = prefix[0]
    trade = LocalTrade(
        pair=active["pair"], is_short=True, enter_tag=active["enter_tag"],
        leverage=active["leverage"], trading_mode=TradingMode.FUTURES,
        fee_open=active["fee_open"], open_rate=first["safe_price"], amount=first["amount"],
    )
    trade.orders = [
        Order(
            ft_order_side=row["ft_order_side"], ft_is_open=False, status="closed",
            filled=row["amount"], average=row["safe_price"], _trade_bt=trade,
            order_filled_date=pd.Timestamp(row["order_filled_timestamp"], unit="ms", tz="UTC"),
        )
        for row in prefix
    ]
    filled = [order.stake_amount_filled for order in trade.select_filled_orders(trade.entry_side)]
    if not isclose(filled[0], request["first_stake"], rel_tol=0, abs_tol=1e-8):
        raise ValueError("Reconstructed first stake differs from the recorded request")
    if not isclose(fsum(filled), request["filled_entry_stake"], rel_tol=0, abs_tol=1e-8):
        raise ValueError("Native reconstructed entry orders differ from request-time filled margin")
    return trade


def cutoff_check(strategy, config, requests):
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.enums import CandleType

    config["candle_type_def"] = CandleType.FUTURES
    provider = DataProvider(config, None)
    strategy.dp = provider
    seen = []
    for pair in sorted({row["pair"] for row in requests}):
        first = next(row for row in requests if row["pair"] == pair)
        at = pd.Timestamp(first["time"])
        dates = pd.DatetimeIndex([at - 2 * FIVE_MINUTES, at - FIVE_MINUTES,
                                  at, at + FIVE_MINUTES])
        provider._set_cached_df(pair, "5m", pd.DataFrame({"date": dates}), CandleType.FUTURES)
        provider._set_dataframe_max_date(at)
        provider._set_dataframe_max_index(pair, 2)
        visible, _ = provider.get_analyzed_dataframe(pair, "5m")
        if visible.date.tolist() != dates[:2].tolist():
            raise ValueError(f"Unexpected analyzed-data view at callback for {pair}")
        seen.append({"pair": pair, "event_time": at.isoformat(),
                     "last_visible": dates[1].isoformat()})
    return seen


def verify(frozen):
    from freqtrade import __file__ as engine_file
    from freqtrade.enums import RunMode
    from freqtrade.resolvers import StrategyResolver

    registered = read(checked(frozen["new_plan"]))
    engine = Path(registered["engine_source"]) / "freqtrade/__init__.py"
    if Path(engine_file).resolve() != engine:
        raise ValueError("Wrong native engine for recorded-request check")
    candidate, = registered["candidates"]
    requests = [json.loads(line) for line in checked(frozen["requests"]).read_text().splitlines()]
    book = stats_from(checked(frozen["archive"]))["trades"]
    with TemporaryDirectory(prefix="nfi-recorded-callbacks-") as temp:
        path = Path(temp)
        for filename, record in candidate["source_files"].items():
            shutil.copyfile(checked(record), path / filename)
        config = read(checked(candidate["config"]))
        config.update(strategy_path=temp, user_data_dir=path, runmode=RunMode.BACKTEST)
        strategy = StrategyResolver.load_strategy(config)
        strategy.ft_bot_start()
        if strategy.target_profit_cache.data or strategy.target_profit_cache.path.parent != path:
            raise ValueError("Historical callbacks did not start with an isolated empty cache")
        parent = next(cls for cls in type(strategy).__mro__ if cls.__name__ == "FlatTopBoost")
        for row in requests:
            clock = pd.Timestamp(row["time"])
            trade = order_state(book, row, clock)
            with patch.object(parent, "adjust_trade_position", return_value=row["rejected_stake"]):
                if strategy.adjust_trade_position(trade, clock) is not None:
                    raise ValueError("Adapter accepted a previously blocked native request")
        emitted_lines = strategy.blocked_adjustments.read_text().splitlines()
        emitted = [json.loads(line) for line in emitted_lines]
        if emitted != requests:
            raise ValueError("Cap trace differs from the native historical request record")
        visible = cutoff_check(strategy, config, requests)
    return {"historical_requests_reproduced": len(requests),
            "by_pair": dict(Counter(row["pair"] for row in requests)),
            "native_callback_view_checks": visible, "clean_cache": True,
            "engine_commit": registered["engine_commit"], "new_wallet_accounts": 0}


def main():
    if len(sys.argv) == 2:
        print(json.dumps(verify(read(Path(sys.argv[1])))))
        return
    old = receipt(HERE.parent / "nfi80_online_rebuy_cap_20261125_v1/comparison_15bp.json")
    benchmark = read(checked(old))["candidate"]
    preflight = receipt(HERE / "short_rebuy_14_wallet_preflight.json")
    new = read(checked(preflight))["by_cost"]["15"]["plan"]
    freeze = write_once(HERE / "short_rebuy_native_callbacks_freeze.json", {
        "implementation": receipt(Path(__file__)),
        "historical_12_slot_capped_account": old,
        "registered_14_slot_preflight": preflight, "new_plan": new,
        "archive": selected_receipt(benchmark["sources"], ".zip"),
        "requests": benchmark["short_rebuy_cap"]["trace"],
        "orders_enabled": False,
    })
    engine = read(checked(new))["engine_source"]
    module = __spec__.name
    boot = (
        "import runpy,sys; sys.path.insert(0,sys.argv.pop(1));"
        "runpy.run_module(sys.argv.pop(1),run_name='__main__')"
    )
    command = [sys.executable, "-B", "-c", boot, engine, module, str(checked(freeze))]
    result = json.loads(subprocess.check_output(command, text=True))
    write_once(HERE / "short_rebuy_native_callbacks.json", {
        "freeze": freeze, "analysis": result,
        "scope": (
            "Replay the cap on actual request values from the earlier twelve-slot native "
            "account and native filled-order prefixes. The original request-generating "
            "strategy branch is supplied the recorded request. Candle slicing is checked "
            "at the same first request clocks. This is a callback diagnostic only."
        ),
        "orders_enabled": False,
    })
    print(result)


if __name__ == "__main__":
    main()
