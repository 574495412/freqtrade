"""Re-evaluate the pre-adjustment NEAR exit callbacks at the two observed costs."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from research.crypto.head_shoulders_financial_native_20261117_v3 import independent_account_audit_v2
from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .annual_trade_scale import FIVE_MINUTES
from .continuous_native_inputs import NativeShardBinding
from .inputs import HERE
from .native_trade_path_review import price_reader, timestamp
from .volatility_across_years import selected_receipt


PAIR = "NEAR/USDT:USDT"


def open_trade(export, fee):
    from freqtrade.enums import TradingMode
    from freqtrade.persistence import LocalTrade, Order

    first = export["orders"][0]
    opened = timestamp(first["order_filled_timestamp"])
    trade = LocalTrade(
        id=1, pair=PAIR, is_short=True, enter_tag=export["enter_tag"],
        trading_mode=TradingMode.FUTURES, leverage=export["leverage"], open_date=opened,
        fee_open=fee, fee_close=fee, open_rate=first["safe_price"], amount=first["amount"],
        stake_amount=first["amount"] * first["safe_price"] / export["leverage"],
    )
    order = Order(
        ft_order_side="sell", ft_is_open=False, status="closed", filled=first["amount"],
        average=first["safe_price"], _trade_bt=trade, order_filled_date=opened,
    )
    trade.orders = [order]
    return trade, order


def replay(plan, export, first_fill, stop, fee):
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.enums import CandleType, RunMode
    from freqtrade.persistence import CustomDataWrapper
    from freqtrade.resolvers import StrategyResolver

    candidate, = plan["candidates"]
    schedule = read(checked(plan["historical_schedule"]))["days"]
    universe = schedule[first_fill.strftime("%Y-%m-%d")]["pairs"]
    last_open = pd.date_range(first_fill, stop, freq=FIVE_MINUTES).as_unit("ns")
    entry, *later = export["orders"]
    if not entry["ft_is_entry"] or any(
        row["order_filled_timestamp"] < stop.value // 1_000_000 for row in later
    ):
        raise ValueError("This callback diagnostic requires one unchanged entry before the cutoff")
    with TemporaryDirectory(prefix="nfi-near-exit-") as directory:
        folder = Path(directory)
        for name, record in candidate["source_files"].items():
            shutil.copyfile(checked(record), folder / name)
        config = read(checked(candidate["config"]))
        start = int(pd.Timestamp(plan["start"]).timestamp())
        # Include the observed execution-open candle, but do not pass it as an analyzed row.
        config.update(
            timerange=f"{start}-{int((stop + FIVE_MINUTES).timestamp())}",
            datadir=Path(plan["datadir"]), user_data_dir=folder, strategy_path=directory,
            runmode=RunMode.BACKTEST, candle_type_def=CandleType.FUTURES, fee=fee,
        )
        strategy = StrategyResolver.load_strategy(config)
        binding = NativeShardBinding(plan, config["timeframe"])
        binding.install()
        try:
            provider = DataProvider(config, None, pairlists=SimpleNamespace(whitelist=universe))
            binding.bind_provider(provider)
            provider._set_dataframe_max_date(stop)
            strategy.dp = provider
            strategy.ft_bot_start()
            if strategy.target_profit_cache.data:
                raise ValueError("Exit cache was not isolated")
            raw = provider.historic_ohlcv(PAIR, "5m")
            execution = raw.set_index("date")["open"].reindex(last_open)
            if not np.isfinite(execution).all():
                raise ValueError("Missing native execution-open candles")
            closed = raw.loc[raw.date < stop].copy()
            computed = strategy.advise_all_indicators({PAIR: closed})[PAIR]
            analyzed = strategy.ft_advise_signals(computed, {"pair": PAIR})
            provider._set_cached_df(PAIR, "5m", analyzed, CandleType.FUTURES)
            CustomDataWrapper.use_db = False
            CustomDataWrapper.reset_custom_data()
            trade, order = open_trade(export, fee)
            strategy.order_filled(PAIR, trade, order, first_fill)
            _, events, cash = price_reader(plan)(PAIR)
            chosen = (events >= export["open_timestamp"]) & (events <= export["close_timestamp"])
            # Hourly backtesting funding is refreshed before the exit callback.
            clocks = last_open.as_unit("ms").asi8 + (last_open == last_open.floor("h"))
            funding, _, _ = independent_account_audit_v2.funding_history(
                export, events[chosen], cash[chosen], clocks,
            )
            return callbacks(strategy, provider, analyzed, trade, last_open, execution, funding)
        finally:
            binding.history.load_data = binding.source_load
            binding.history_utils.get_datahandler = binding.source_handler


def callbacks(strategy, provider, analyzed, trade, dates, execution, funding):
    candle_dates = pd.DatetimeIndex(analyzed.date)
    snapshots = []
    for date, price, amount in zip(dates, execution, funding, strict=True):
        provider._set_dataframe_max_date(date)
        provider._set_dataframe_max_index(PAIR, int(candle_dates.searchsorted(date, side="left")))
        previous, _ = provider.get_analyzed_dataframe(PAIR, "5m")
        if previous.date.iloc[-1] != date - FIVE_MINUTES:
            raise ValueError("The exit callback would not end on the last closed candle")
        trade.funding_fees = amount
        before = (strategy.target_profit_cache.data.get(PAIR) or {}).copy()
        reason = strategy.custom_exit(PAIR, trade, date, price, trade.calc_profit_ratio(price))
        after = (strategy.target_profit_cache.data.get(PAIR) or {}).copy()
        if before != after or date == dates[-1] or reason:
            ratio = strategy.calc_total_profit(trade, trade.orders, [], price)[-1]
            snapshots.append({
                "time": date.isoformat(), "execution_open": float(price),
                "funding_usdt": float(amount), "profit_per_initial_notional": ratio,
                "cache_before": before, "cache_after": after, "exit_reason": reason,
            })
        if reason:
            break
    return {"checked_callbacks": int(dates.get_loc(date)) + 1, "snapshots": snapshots}


def compute(frozen):
    from freqtrade import __file__ as engine_file

    first_fill = pd.Timestamp(frozen["observed_first_fill"])
    cutoff = pd.Timestamp(frozen["observed_8bp_exit"])
    results = {}
    for cost, record in frozen["accounts"].items():
        account = read(checked(record))["account"]
        source = read(checked(account["registered_plan"]))
        if Path(engine_file).resolve() != Path(source["engine_source"]) / "freqtrade/__init__.py":
            raise ValueError("Wrong Freqtrade engine for the historical callback check")
        proof = read(checked(account["proof"]))
        archive = selected_receipt(proof["sources"], ".zip")
        campaign, = [
            row for row in stats_from(checked(archive))["trades"]
            if row["pair"] == PAIR and row["is_short"]
            and timestamp(row["open_timestamp"]) == first_fill
        ]
        with patch("socket.socket.connect", side_effect=RuntimeError("Offline callback")):
            report = replay(source, campaign, first_fill, cutoff, int(cost) / 10000)
        last = report["snapshots"][-1]
        results[cost] = {
            "native_observed_exit": timestamp(campaign["close_timestamp"]).isoformat(),
            "native_observed_reason": campaign["exit_reason"], "callback_diagnostic": report,
            "matches_early_8bp_exit": last["time"] == cutoff.isoformat()
            and last["exit_reason"] == campaign["exit_reason"],
        }
    return results


def main():
    if len(sys.argv) == 2:
        print(json.dumps(compute(read(Path(sys.argv[1]))), allow_nan=False))
        return
    prior = receipt(HERE / "recent_two_year_cost_attribution.json")
    match = read(checked(prior))
    source = match["verified_accounts"]["8"]
    plan = read(checked(read(checked(source))["account"]["registered_plan"]))
    ref = write_once(HERE / "near_short_exit_freeze.json", {
        "source": receipt(Path(__file__)), "original_path_evidence": prior,
        "accounts": match["verified_accounts"],
        "observed_first_fill": match["near_562_by_cost"]["8"]["first_fill"],
        "observed_8bp_exit": match["near_562_by_cost"]["8"]["close"], "orders_enabled": False,
    })
    boot = (
        "import runpy,sys; sys.path.insert(0,sys.argv.pop(1));"
        "runpy.run_module(sys.argv.pop(1),run_name='__main__')"
    )
    args = [sys.executable, "-B", "-c", boot, plan["engine_source"], __spec__.name,
            str(checked(ref))]
    observed = json.loads(subprocess.check_output(args, text=True))
    write_once(HERE / "near_short_exit_audit.json", {
        "freeze": ref, "results": observed, "orders_enabled": False, "new_wallet_accounts": 0,
        "scope": (
            "Observed single funded position in each cost scenario through the earlier exit. "
            "Reconstruct its original custom-exit callback and cache in isolation. "
            "No order placement or altered trade-path economics are inferred."
        ),
    })
    for cost, row in observed.items():
        print(cost, row["matches_early_8bp_exit"], row["callback_diagnostic"]["snapshots"][-1])


if __name__ == "__main__":
    main()
