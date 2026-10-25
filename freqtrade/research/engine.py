"""使用Freqtrade原生账户；额外计算小时浮亏观察和小额容量诊断。"""

import logging
import os
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.commands.optimize_commands import setup_optimize_configuration
from freqtrade.enums import RunMode
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.resolvers import ExchangeResolver

from .data import read, save, sha


def configuration(plan: dict, dataset: Path, output: Path, start: str, end: str, cost: int) -> dict:
    return {
        "dry_run": True,
        "dry_run_wallet": plan["initial_equity_usdt"],
        "stake_currency": "USDT",
        "stake_amount": plan["stake_usdt"],
        "tradable_balance_ratio": 0.99,
        "max_open_trades": plan["max_open_trades"],
        "fiat_display_currency": "",
        "timeframe": plan["timeframe"],
        "trading_mode": plan["trading_mode"],
        "margin_mode": plan["margin_mode"],
        "fee": plan["fee_per_side"] + cost / 10000,
        "strategy_list": plan["candidates"],
        "strategy": plan["candidates"][0],
        "strategy_path": str(Path(__file__).parent),
        "recursive_strategy_search": False,
        "datadir": str(dataset / "candles"),
        "user_data_dir": str(output),
        # 原生TimeRange包含终点，显式减一秒防止读入下一评价段首根K线。
        "timerange": (
            f"{int(pd.Timestamp(start, tz='UTC').timestamp())}-"
            f"{int(pd.Timestamp(end, tz='UTC').timestamp()) - 1}"
        ),
        "dataformat_ohlcv": "feather",
        "export": "trades",
        "backtest_cache": "none",
        "exportdirectory": str(output),
        "db_url": "sqlite://",
        "pairlists": [{"method": "StaticPairList"}],
        "exchange": {
            "name": plan["exchange"],
            "key": "",
            "secret": "",
            "enable_ws": False,
            "pair_whitelist": [
                symbol.removesuffix("USDT") + "/USDT:USDT" for symbol in plan["symbols"]
            ],
            "pair_blacklist": [],
            "ccxt_config": {
                "timeout": 15000,
                "options": {
                    "fetchMarkets": {"types": ["linear"]},
                    "fetchCurrencies": False,
                },
            },
        },
        "entry_pricing": {"price_side": "other", "use_order_book": True, "order_book_top": 1},
        "exit_pricing": {"price_side": "other", "use_order_book": True, "order_book_top": 1},
        "unfilledtimeout": {"entry": 10, "exit": 10, "unit": "minutes"},
        "internals": {"process_throttle_secs": 5},
    }


def equity_observations(
    stats: dict, market: dict, funding: dict, start: str, end: str, initial: float
) -> pd.DataFrame:
    """从原生成交重估每小时开盘权益，保留未实现损益；不是另一套撮合器。"""
    clocks = pd.date_range(start, end, freq="h", inclusive="left", tz="UTC")
    times = clocks.as_unit("ns").asi8
    realized = np.zeros(len(clocks))
    floating = np.zeros(len(clocks))
    for trade in stats["trades"]:
        opened, closed = pd.Timestamp(trade["open_date"]), pd.Timestamp(trade["close_date"])
        first, last = times.searchsorted(opened.value), times.searchsorted(closed.value)
        if last < len(times):
            realized[last] += trade["profit_abs"]
        if last <= first:
            continue
        pair, amount = trade["pair"], trade["amount"]
        prices = (
            market[pair].set_index("date")["open"].reindex(clocks[first:last]).to_numpy(dtype=float)
        )
        if not np.isfinite(prices).all():
            raise ValueError("浮亏观察缺少真实小时价格")
        direction = -1 if trade["is_short"] else 1
        upnl = direction * amount * (prices - trade["open_rate"])
        upnl -= amount * trade["open_rate"] * trade["fee_open"]
        fees = funding[pair]
        event_times = fees["_ff_date_ns"].to_numpy()
        prefix = np.r_[0.0, np.cumsum(fees["_ff_fee_per_unit"].to_numpy())]
        lower = event_times.searchsorted(opened.value, "left")
        upper = event_times.searchsorted(times[first:last], "right")
        upnl -= direction * amount * (prefix[upper] - prefix[lower])
        floating[first:last] += upnl
    equity = initial + np.cumsum(realized) + floating
    peaks = np.maximum.accumulate(np.r_[initial, equity])[1:]
    return pd.DataFrame({"date": clocks, "equity": equity, "drawdown": 1 - equity / peaks})


def summarize(stats: dict, equity: pd.DataFrame, quotes: pd.DataFrame, plan: dict) -> dict:
    ratios = []
    indexed = quotes.set_index(["pair", "date"])["quote_volume"]
    for trade in stats["trades"]:
        clock = pd.Timestamp(trade["open_date"]) - pd.Timedelta(hours=1)
        volume = float(indexed.loc[(trade["pair"], clock)])
        if volume <= 0:
            raise ValueError("入场前一小时无成交额，不能声明容量可用")
        ratios.append(trade["amount"] * trade["open_rate"] / volume)
    return {
        "strategy": stats["strategy_name"],
        "closed_trades": stats["total_trades"],
        "net_return": stats["profit_total"],
        "net_profit_usdt": stats["profit_total_abs"],
        "win_rate": stats["wins"] / stats["total_trades"] if stats["total_trades"] else None,
        "profit_factor": stats["profit_factor"],
        "hourly_observed_drawdown": float(equity["drawdown"].max()),
        "native_closed_trade_drawdown": stats["max_drawdown_account"],
        "maximum_prior_hour_participation": max(ratios, default=0.0),
        "capacity_review_required": max(ratios, default=0.0) > plan["capacity_review_ratio"],
        "funding_net_usdt": sum(trade["funding_fees"] for trade in stats["trades"]),
        "by_pair": [
            {key: row[key] for key in ("key", "trades", "profit_total", "profit_total_abs")}
            for row in stats["results_per_pair"]
        ],
        "economic_status": "NO_TRADES" if not stats["total_trades"] else "EVALUATED",
        "drawdown_resolution": "hourly_open_marked_not_intrahour_worst_case",
    }


def run_phase(plan: dict, dataset: Path, output: Path, phase: str) -> dict:
    """全部候选和成本一并保留；原生撮合、风险、资金费与退出不重写。"""
    output.mkdir(parents=True, exist_ok=False)
    if read(dataset / "plan.json") != plan:
        raise ValueError("数据与当前计划不一致")
    for name, identity in read(dataset / "receipts.json").items():
        if sha(dataset / name) != identity:
            raise ValueError("数据文件在准备后发生改变")
    if any(key.startswith("FREQTRADE__") for key in os.environ):
        raise ValueError("本轮研究不接受FREQTRADE环境配置覆盖")
    if phase == "continuous":
        start, end = plan["validation_start"], plan["holdout_end"]
    else:
        start, end = plan[phase + "_start"], plan[phase + "_end"]
    quotes = pd.read_parquet(dataset / "quote_volume.parquet")
    summaries = []
    exchange = None
    try:
        for cost in plan["extra_cost_bps_per_side"]:
            folder = output / f"cost_{cost}bps"
            folder.mkdir()
            raw = configuration(plan, dataset, folder, start, end, cost)
            config_path = folder / "settings.json"
            save(config_path, raw)
            config = setup_optimize_configuration(
                {"config": [str(config_path)], "datadir": str(dataset / "candles")},
                RunMode.BACKTEST,
            )
            # 环境变量不允许改变本次冻结的研究配置或引入真实账户。
            for key in ("dry_run", "fee", "stake_amount", "trading_mode", "margin_mode"):
                if config[key] != raw[key]:
                    raise ValueError(f"外部环境改变研究设置: {key}")
            if exchange is None:
                exchange = ExchangeResolver.load_exchange(config, load_leverage_tiers=True)
                save(
                    output / "market_rules.json",
                    {
                        "markets": {
                            pair: exchange.markets[pair]
                            for pair in raw["exchange"]["pair_whitelist"]
                        },
                        "leverage_tiers": exchange._leverage_tiers,
                        "historical_rules_verified": False,
                    },
                )
            engine = Backtesting(deepcopy(config), exchange=exchange)
            engine.progress = None
            logging.getLogger("freqtrade").setLevel(logging.WARNING)
            engine.start()
            for name, stats in engine.results["strategy"].items():
                stats.setdefault("strategy_name", name)
                market, _ = engine.load_bt_data()
                observations = equity_observations(
                    stats, market, engine.futures_data, start, end, plan["initial_equity_usdt"]
                )
                observations.to_parquet(folder / f"{name}_equity.parquet", index=False)
                summary = summarize(stats, observations, quotes, plan)
                summary["extra_cost_bps_per_side"] = cost
                summaries.append(summary)
            save(folder / "summary.json", summaries[-len(plan["candidates"]) :])
    finally:
        if exchange is not None:
            exchange.close()
    result = {
        "schema": "crypto-quant-phase.v1",
        "phase": phase,
        "start": start,
        "end": end,
        "summaries": summaries,
        "orders_enabled": False,
        "validation_results_included": phase != "train",
    }
    save(output / "result.json", result)
    return result
