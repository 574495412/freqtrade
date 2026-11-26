"""Actual native factor calculation, author fixtures, prefix and scoped dedup."""

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha

HERE = Path(__file__).resolve().parent


def main():
    plan = json.loads((HERE / "plan.json").read_text())
    spec = importlib.util.spec_from_file_location("liquidity_runner", HERE / "native_runner.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    for c in plan["candidates"]:
        runner.verify(plan, c)
    sys.path.insert(0, plan["engine_source"])
    sys.path.insert(0, str(HERE))
    from edge_author import edge
    from SpreadFeatures import calculate_features, targets
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.enums import RunMode, CandleType
    from freqtrade.resolvers import StrategyResolver, ExchangeResolver

    fixtures = []
    for filename, value in [
        ("ohlc.csv", 0.0101849034905478),
        ("ohlc-miss.csv", 0.01013284969780197),
    ]:
        raw = pd.read_csv(HERE / "source/bidask/pseudocode" / filename)
        actual = edge(*(raw[c].to_numpy() for c in ["Open", "High", "Low", "Close"]))
        if abs(actual - value) > 1e-14:
            raise ValueError("Author numerical fixture mismatch")
        fixtures.append({"fixture": filename, "actual": actual, "author_expected": value})
    records = []
    features = None
    for candidate in plan["candidates"]:
        if candidate["capital"] != 1000:
            continue
        config = json.loads(Path(candidate["config"]["path"]).read_text())
        config.update(
            strategy_path=str(HERE),
            user_data_dir=Path(plan["output_root"]),
            datadir=Path(plan["datadir"]),
            runmode=RunMode.BACKTEST,
            candle_type_def=CandleType.FUTURES,
            timeframe="1h",
            startup_candle_count=72,
            timerange=f"{int(pd.Timestamp(plan['start']).timestamp())}-{int(pd.Timestamp(plan['end']).timestamp()) - 1}",
        )
        owner = StrategyResolver.load_strategy(config)
        exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
        snapshot = json.loads(Path(plan["market_snapshot"]["path"]).read_text())
        exchange._markets = snapshot["markets"]
        exchange._leverage_tiers = snapshot["leverage_tiers"]
        exchange._api.set_markets(snapshot["markets"], snapshot["currencies"])
        exchange._api_async.set_markets(snapshot["markets"], snapshot["currencies"])
        owner.dp = DataProvider(config, exchange)
        owner.bot_start()
        quotes = pd.read_parquet(config["research_quote_volume"])
        checks = []
        for pair in plan["pairs"]:
            daily = owner.dp.get_pair_dataframe(pair, "1d", "futures")
            baseline = owner.research_features[pair]
            dollars = quotes.loc[quotes.pair.eq(pair)].set_index("date").quote_volume
            for edge_date in ["2025-07-01T00:00Z", "2026-01-01T00:00Z", "2026-08-01T00:00Z"]:
                cutoff = pd.Timestamp(edge_date)
                past = daily.date < cutoff
                prefix = calculate_features(daily.loc[past], dollars.loc[dollars.index < cutoff])
                changed = daily.copy()
                changed.loc[~past, ["open", "high", "low", "close"]] *= 3
                volume = dollars.copy()
                volume.loc[volume.index >= cutoff] *= 7
                perturbed = calculate_features(changed, volume)
                expected = baseline.loc[baseline.decision_date <= cutoff].reset_index(drop=True)
                pd.testing.assert_frame_equal(
                    expected, prefix.reset_index(drop=True), check_exact=True
                )
                pd.testing.assert_frame_equal(
                    expected,
                    perturbed.loc[perturbed.decision_date <= cutoff].reset_index(drop=True),
                    check_exact=True,
                )
            hours = owner.dp.get_pair_dataframe(pair, "1h", "futures")
            native = owner.advise_exit(
                owner.advise_entry(
                    owner.advise_indicators(hours.copy(), {"pair": pair}), {"pair": pair}
                ),
                {"pair": pair},
            )
            if (
                native.loc[
                    native.date.dt.hour.ne(0),
                    ["enter_long", "enter_short", "exit_long", "exit_short"],
                ]
                .ne(0)
                .any()
                .any()
            ):
                raise ValueError("Unscheduled native signals")
            checks.append(
                {
                    "pair": pair,
                    "native_daily_rows": len(daily),
                    "prefix_future_checks": 3,
                    "native_hourly_rows": len(native),
                }
            )
        combined = pd.concat(
            [f.assign(pair=p) for p, f in owner.research_features.items()], ignore_index=True
        )
        combined.to_parquet(
            Path(plan["output_root"]) / f"preflight_features_{candidate['strategy']}.parquet",
            index=False,
        )
        save(
            Path(plan["output_root"]) / f"preflight_targets_{candidate['strategy']}.json",
            owner._targets,
        )
        if features is None:
            features = combined
        else:
            pd.testing.assert_frame_equal(features, combined, check_exact=True)
        records.append({"strategy": candidate["strategy"], "checks": checks})
        exchange.close()
    days = pd.date_range(plan["start"], plan["end"], freq="1D", inclusive="left")
    correlations = []
    for day in days:
        part = features.loc[features.decision_date.eq(day)].set_index("pair")
        if len(part) != 6 or not np.isfinite(part[["edge30", "amihud30", "lowvol63"]]).all().all():
            raise ValueError("Incomplete common daily factor support")
        correlations.append(
            part[["edge30", "amihud30", "lowvol63"]].corr().iloc[0, 1:].abs().to_numpy()
        )
    average = np.mean(correlations, axis=0)
    if (average > 0.99).any():
        raise ValueError("Edge duplicates a scoped control;stop before finance")
    save(
        HERE / "preflight.json",
        {
            "passed": True,
            "author_fixtures": fixtures,
            "actual_native_strategies": records,
            "full_common_days": len(days),
            "factor_cells": len(days) * 6 * 3,
            "scoped_mean_abs_daily_pearson": {
                "Edge_Amihud": float(average[0]),
                "Edge_LowVol": float(average[1]),
            },
            "all_registry_dedup_claim": False,
            "independent_oos_certified": False,
        },
    )
    print(
        json.dumps(
            {
                "passed": True,
                "full_days": len(days),
                "factor_cells": len(days) * 6 * 3,
                "mean_abs_peer_correlation": average.tolist(),
            }
        )
    )


if __name__ == "__main__":
    main()
