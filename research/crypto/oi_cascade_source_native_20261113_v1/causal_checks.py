"""Actual native informative prefix/perturbation checks, not a profitability test."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha


HERE = Path(__file__).resolve().parent


def main():
    plan = json.loads((HERE / "plan.json").read_text())
    sys.path.insert(0, plan["engine_source"])
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.enums import CandleType, RunMode
    from freqtrade.resolvers import ExchangeResolver, StrategyResolver

    prices = pd.read_feather(Path(plan["datadir"]) / "futures/BTC_USDT_USDT-1h-futures.feather")
    prices = prices.loc[
        prices.date.ge(pd.Timestamp(plan["start"]) - pd.Timedelta(hours=60))
        & prices.date.lt(pd.Timestamp(plan["end"]))
    ].reset_index(drop=True)
    snapshot = json.loads(Path(plan["market_snapshot"]["path"]).read_text())
    records = []
    for candidate in plan["candidates"]:
        config = json.loads(Path(candidate["config"]["path"]).read_text())
        config.update(
            datadir=Path(plan["datadir"]),
            user_data_dir=Path(plan["output_root"]),
            strategy_path=str(HERE),
            runmode=RunMode.BACKTEST,
            candle_type_def=CandleType.FUTURES,
            timeframe="1h",
            startup_candle_count=60,
            timerange=f"{int(pd.Timestamp(plan['start']).timestamp())}-{int(pd.Timestamp(plan['end']).timestamp()) - 1}",
        )
        owner = StrategyResolver.load_strategy(config)
        exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
        exchange._markets = snapshot["markets"]
        exchange._leverage_tiers = snapshot["leverage_tiers"]
        exchange._api.set_markets(snapshot["markets"], snapshot["currencies"])
        exchange._api_async.set_markets(snapshot["markets"], snapshot["currencies"])
        owner.dp = DataProvider(config, exchange)
        original_provider = owner.dp.get_pair_dataframe

        def calculate(frame):
            return owner.advise_entry(
                owner.advise_indicators(frame.copy(), {"pair": plan["pairs"][0]}),
                {"pair": plan["pairs"][0]},
            )

        baseline = calculate(prices)
        for edge in ["2025-07-01T00:00Z", "2026-01-01T00:00Z", "2026-08-01T00:00Z"]:
            edge = pd.Timestamp(edge)
            prefix = prices.loc[prices.date.le(edge)].copy()
            short = calculate(prefix)
            changed = prices.copy()
            changed.loc[changed.date.gt(edge), ["open", "high", "low", "close"]] *= 3

            def perturbed_provider(*args, **kwargs):
                frame = original_provider(*args, **kwargs).copy()
                future = frame.date.gt(edge)
                for field in ["open_interest_amount", "open_interest_value"]:
                    if field in frame:
                        frame.loc[future, field] *= 7
                return frame

            owner.dp.get_pair_dataframe = perturbed_provider
            perturbed = calculate(changed)
            owner.dp.get_pair_dataframe = original_provider
            for field in ["enter_long", "enter_short"]:
                before = baseline.loc[prefix.index, field].to_numpy()
                if not np.array_equal(before, short[field].to_numpy()):
                    raise ValueError("Actual native source prefix mismatch")
                if not np.array_equal(before, perturbed.loc[prefix.index, field].to_numpy()):
                    raise ValueError("Future price/OI perturbation changed a past native signal")
            records.append(
                {
                    "strategy": candidate["name"],
                    "last_indicator_bar_open": str(edge),
                    "past_signal_rows": len(prefix),
                    "both_directions_exact": True,
                    "actual_native_informative_merge": True,
                }
            )
        exchange.close()
    save(
        HERE / "causal_checks.json",
        {
            "passed": True,
            "actual_prefix_and_joint_price_oi_perturbations": records,
            "code_sha256": sha(HERE / "causal_checks.py"),
            "scope": "3cutoffs x3strategies; not official lookahead/recursive, historical dissemination or independent alpha certification.",
        },
    )
    print(json.dumps({"native_prefix_perturbation_groups": len(records), "passed": True}))


if __name__ == "__main__":
    main()
