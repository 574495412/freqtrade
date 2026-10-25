"""Count native entry signals and capital/minimum-order feasibility; no future PnL labels."""

import argparse
import gc
import math
from pathlib import Path

import pandas as pd

from freqtrade.commands.optimize_commands import setup_optimize_configuration
from freqtrade.enums import RunMode
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.research.data import read, save, sha
from freqtrade.research.engine import configuration


def census(plan_path, output):
    if output.exists():
        raise ValueError("Preserve prior census")
    plan, exchange, records = read(plan_path), None, []
    output.mkdir(parents=True)
    strategies = Path(
        "user_data/crypto_quant/nfi_lifecycle_run_v002/train/NFIBoundedPartial/cost_3bps/strategies"
    ).absolute()
    for source in plan["strategy_sources"]:
        if sha(strategies / Path(source["path"]).name) != source["sha256"]:
            raise ValueError("Executed strategy copy changed")
    try:
        for coin in ["BTC", "ETH", "SOL", "IOTX"]:
            pair, folder = f"{coin}/USDT:USDT", output / coin
            folder.mkdir()
            adapted = dict(
                plan,
                candidates=["NFIBoundedPartial"],
                stake_usdt="unlimited",
                trading_mode="futures",
                margin_mode="isolated",
                exchange="binance",
            )
            config = configuration(
                adapted, Path(plan["dataset"]), folder, plan["train_start"], plan["train_end"], 3
            )
            config.pop("strategy_list")
            config.update(strategy_path=str(strategies), **plan["settings"])
            config["exchange"]["pair_whitelist"] = [pair]
            save(folder / "settings.json", config)
            native = setup_optimize_configuration(
                {
                    "config": [str(folder / "settings.json")],
                    "datadir": str(Path(plan["dataset"]) / "candles"),
                },
                RunMode.BACKTEST,
            )
            engine = Backtesting(native, exchange)
            exchange = engine.exchange
            engine._set_strategy(engine.strategylist[0])
            data, _ = engine.load_bt_data()
            frame = engine.strategy.ft_advise_signals(
                engine.strategy.advise_all_indicators(data)[pair], {"pair": pair}
            )
            frame["next_open"] = frame.open.shift(-1)
            selected = frame.loc[
                (frame.date >= pd.Timestamp(plan["train_start"], tz="UTC"))
                & frame.enter_long.eq(1)
                & frame.next_open.notna()
            ]
            counts = {
                str(capital): {
                    "raw_signals": len(selected),
                    "unrecognized_tag": 0,
                    "atr_ceiling_rejected": 0,
                    "minimum_stake_rejected": 0,
                    "feasible_before_portfolio_and_slippage": 0,
                }
                for capital in [1000, 100000]
            }
            # Tag classification duplicated independently from the strategy.
            allowed = {
                str(i)
                for i in [*range(1, 10), 21, 22, 23, *range(41, 46), 61, 81, 82, *range(101, 106)]
            }
            for row in selected.itertuples(index=False):
                tokens = set(row.enter_tag.split())
                sleeve = "core" if tokens == {"61"} else "small"
                distance = max(
                    0.10 if sleeve == "core" else 0.06,
                    (8 if sleeve == "core" else 4) * row.bounded_atr / row.next_open,
                )
                minimum = exchange.get_min_pair_stake_amount(
                    pair, row.next_open, engine.strategy.stoploss, 2
                )
                for capital in [1000, 100000]:
                    result = counts[str(capital)]
                    if not tokens or not tokens <= allowed:
                        result["unrecognized_tag"] += 1
                    elif (
                        not math.isfinite(row.bounded_atr)
                        or row.bounded_atr <= 0
                        or not 0 < distance <= (0.20 if sleeve == "core" else 0.12)
                    ):
                        result["atr_ceiling_rejected"] += 1
                    else:
                        budget = capital * 0.99 * (0.005 if sleeve == "core" else 0.001)
                        margin = capital * 0.99 * (0.20 if sleeve == "core" else 0.02)
                        fee = config["fee"]
                        stake = min(
                            0.99 * budget / (4 * (distance + fee * (2 - distance))), margin / 2
                        )
                        key = (
                            "minimum_stake_rejected"
                            if minimum is not None and stake < minimum
                            else "feasible_before_portfolio_and_slippage"
                        )
                        result[key] += 1
            record = {
                "coin": coin,
                "signal_tags": selected.enter_tag.value_counts().to_dict(),
                "capital_comparison": counts,
            }
            records.append(record)
            print(record, flush=True)
            del engine, data, frame, selected
            gc.collect()
    finally:
        if exchange is not None:
            exchange.close()
    save(
        output / "result.json",
        {
            "plan_sha256": sha(plan_path),
            "source_sha256": sha(Path(__file__)),
            "coins": records,
            "scope": "Four illustrative pairs, raw signal and minimum-stake feasibility only. "
            "Known next-open execution price, no future return labels or economic accounts. "
            "Portfolio capacity, same-pair ownership, exit-signal conflict, slippage confirmation "
            "and actual quantity rounding not modeled. Capital values are a diagnostic comparison, "
            "not qualified live sizing or a changed frozen account plan.",
            "orders_enabled": False,
            "production_eligible": False,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    census(args.plan, args.output)
