"""Actual native forecast merge and signal prefixes for the core risk diagnostic."""

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
    if output.exists():
        raise ValueError("Preserve previous prefix evidence")
    plan, exchange = read(plan_path), None
    dataset = Path(plan["dataset"])
    output.mkdir(parents=True)
    strategies = output / "strategies"
    strategies.mkdir()
    for source in plan["strategy_sources"]:
        path = Path(source["path"])
        if sha(path) != source["sha256"]:
            raise ValueError("Registered strategy source changed")
        shutil.copyfile(path, strategies / path.name)

    def frame(candidate, pair, label, end):
        nonlocal exchange
        folder = output / label
        folder.mkdir()
        adapted = dict(
            plan,
            stake_usdt="unlimited",
            trading_mode="futures",
            margin_mode="isolated",
            exchange="binance",
            candidates=[candidate],
        )
        config = configuration(adapted, dataset, folder, plan["train_start"], end, 3)
        config.pop("strategy_list")
        config.update(strategy_path=str(strategies), **plan["settings"])
        config["exchange"]["pair_whitelist"] = [pair]
        save(folder / "settings.json", config)
        native = setup_optimize_configuration(
            {"config": [str(folder / "settings.json")], "datadir": str(dataset / "candles")},
            RunMode.BACKTEST,
        )
        engine = Backtesting(native, exchange)
        exchange = engine.exchange
        engine._set_strategy(engine.strategylist[0])
        data, _ = engine.load_bt_data()
        indicators = engine.strategy.advise_all_indicators(data)[pair]
        result = engine.strategy.ft_advise_signals(indicators, {"pair": pair})
        del engine, data, indicators
        gc.collect()
        return result

    rows = []
    try:
        for candidate in [c["name"] for c in plan["candidates"]]:
            for coin in ["ETH", "SOL", "XRP"]:
                pair = f"{coin}/USDT:USDT"
                full = frame(candidate, pair, f"{candidate}_full_{coin}", plan["train_end"])
                for number, cut in enumerate(["2024-08-05T12:55:00", "2024-12-14T23:55:00"]):
                    clock = pd.Timestamp(cut, tz="UTC")
                    prefix = frame(candidate, pair, f"{candidate}_prefix_{coin}_{number}", cut)
                    lower = clock - pd.Timedelta(hours=48)
                    a = full.loc[(full.date >= lower) & (full.date < clock)].reset_index(drop=True)
                    b = prefix.loc[(prefix.date >= lower) & (prefix.date < clock)].reset_index(
                        drop=True
                    )
                    pd.testing.assert_frame_equal(a, b, check_exact=True, check_dtype=False)
                    row = {
                        "candidate": candidate,
                        "coin": coin,
                        "cut": clock.isoformat(),
                        "rows": len(a),
                        "columns": len(a.columns),
                        "entry_flags": int(a.enter_long.eq(1).sum() + a.enter_short.eq(1).sum()),
                        "passed": True,
                    }
                    rows.append(row)
                    print(row, flush=True)
                    del prefix
                    gc.collect()
                del full
                gc.collect()
    finally:
        if exchange is not None:
            exchange.close()
    save(
        output / "result.json",
        {
            "passed": len(rows) == 18 and sum(r["entry_flags"] for r in rows) > 0,
            "cases": rows,
            "plan_sha256": sha(plan_path),
            "checker_source_sha256": sha(Path(__file__)),
            "native_source_identity": source_identity(),
            "economic_accounts_computed": False,
            "scope": "Native timestamp forecast join plus direction flags. Model chronology was "
            "audited before export; fixed risk and full exits have separate actual-fill audits.",
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    check(args.plan, args.output)
