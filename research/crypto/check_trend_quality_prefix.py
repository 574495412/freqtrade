"""Real native hourly/daily indicator prefixes across two completed-day boundaries."""

import argparse
import gc
import shutil
from pathlib import Path

import pandas as pd

from freqtrade.commands.optimize_commands import setup_optimize_configuration
from freqtrade.enums import RunMode
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.research.data import read, save, sha
from freqtrade.research.engine import configuration
from freqtrade.research.workflow import source_identity


def check(plan_path, output):
    plan = read(plan_path)
    output.mkdir(parents=True, exist_ok=False)
    sources = output / "strategies"
    sources.mkdir()
    for item in plan["strategy_sources"]:
        path = Path(item["path"])
        if sha(path) != item["sha256"]:
            raise ValueError("Frozen strategy changed")
        shutil.copyfile(path, sources / path.name)
    exchange, rows = None, []

    def frame(pair, label, until):
        nonlocal exchange
        folder = output / label
        folder.mkdir()
        adapted = dict(
            plan,
            candidates=["Donchian20ADX25"],
            stake_usdt=plan["stake_amount"],
            trading_mode="futures",
            margin_mode="isolated",
            exchange="binance",
        )
        raw = configuration(adapted, Path(plan["dataset"]), folder, plan["train_start"], until, 3)
        raw.pop("strategy_list")
        raw.update(strategy_path=str(sources), **plan["settings"])
        raw["exchange"]["pair_whitelist"] = [pair]
        save(folder / "settings.json", raw)
        config = setup_optimize_configuration(
            {
                "config": [str(folder / "settings.json")],
                "datadir": str(Path(plan["dataset"]) / "candles"),
            },
            RunMode.BACKTEST,
        )
        engine = Backtesting(config, exchange)
        exchange = engine.exchange
        engine._set_strategy(engine.strategylist[0])
        data, _ = engine.load_bt_data()
        indicators = engine.strategy.advise_all_indicators(data)[pair]
        result = engine.strategy.ft_advise_signals(indicators, {"pair": pair})
        del engine, data, indicators
        gc.collect()
        return result

    try:
        for coin in ["ETH", "SOL", "XRP"]:
            pair = coin + "/USDT:USDT"
            full = frame(pair, coin + "_full", plan["train_end"])
            for number, cut in enumerate(["2024-03-15T00:00:00", "2024-09-15T00:00:00"]):
                end = pd.Timestamp(cut, tz="UTC")
                prefix = frame(pair, f"{coin}_prefix{number}", cut)
                a = full.loc[
                    full.date.between(end - pd.Timedelta(hours=24), end - pd.Timedelta(hours=1))
                ].reset_index(drop=True)
                b = prefix.loc[
                    prefix.date.between(end - pd.Timedelta(hours=24), end - pd.Timedelta(hours=1))
                ].reset_index(drop=True)
                pd.testing.assert_frame_equal(a, b, check_exact=True, check_dtype=False)
                rows.append(
                    {
                        "pair": pair,
                        "cut": str(end),
                        "rows": len(a),
                        "columns": len(a.columns),
                        "passed": True,
                    }
                )
    finally:
        if exchange is not None:
            exchange.close()
    result = {
        "passed": len(rows) == 6,
        "cases": rows,
        "plan_sha256": sha(plan_path),
        "native_source_identity": source_identity(),
        "checker_sha256": sha(Path(__file__)),
        "scope": "Native daily ADX/channel/ATR and hourly entries; not forward economics",
    }
    save(output / "result.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = check(args.plan.resolve(), args.output.resolve())
    print({"passed": result["passed"], "cases": len(result["cases"])})
