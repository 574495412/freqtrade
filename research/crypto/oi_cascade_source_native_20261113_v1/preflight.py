"""Use actual pinned native DataProvider/informative merge, then scalar audits."""

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
    module_spec = importlib.util.spec_from_file_location("oi_native", HERE / "native_runner.py")
    runner = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(runner)
    for candidate in plan["candidates"]:
        runner.verify(plan, candidate)
    sys.path.insert(0, plan["engine_source"])
    import freqtrade
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.enums import CandleType, RunMode
    from freqtrade.resolvers import ExchangeResolver, StrategyResolver

    if Path(freqtrade.__file__).resolve() != Path(plan["engine_source"]) / "freqtrade/__init__.py":
        raise ValueError("Wrong native package selected")
    prices = pd.read_feather(Path(plan["datadir"]) / "futures/BTC_USDT_USDT-1h-futures.feather")
    prices = prices.loc[
        prices.date.ge(pd.Timestamp(plan["start"]) - pd.Timedelta(hours=60))
        & prices.date.lt(pd.Timestamp(plan["end"]))
    ].reset_index(drop=True)
    raw_oi = pd.read_feather(
        Path(plan["datadir"]) / "futures/BTC_USDT_USDT-1h-open_interest.feather"
    ).set_index("date")
    rows = []
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
        strategy = StrategyResolver.load_strategy(config)
        exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
        snapshot = json.loads(Path(plan["market_snapshot"]["path"]).read_text())
        exchange._markets = snapshot["markets"]
        exchange._leverage_tiers = snapshot["leverage_tiers"]
        exchange._api.set_markets(snapshot["markets"], snapshot["currencies"])
        exchange._api_async.set_markets(snapshot["markets"], snapshot["currencies"])
        strategy.dp = DataProvider(config, exchange)
        native = strategy.advise_indicators(prices.copy(), {"pair": plan["pairs"][0]})
        actual = strategy.advise_entry(native.copy(), {"pair": plan["pairs"][0]})
        native.to_parquet(
            Path(plan["output_root"]) / f"preflight_{candidate['name']}.parquet", index=False
        )
        reference = raw_oi.reindex(pd.DatetimeIndex(actual.date))
        amount = reference.open_interest_amount.reset_index(drop=True)
        # The native loader may drop its final informative row as incomplete.
        finite = actual.open_interest_amount_1h.notna()
        if not np.allclose(
            actual.loc[finite, "open_interest_amount_1h"], amount.loc[finite], rtol=0, atol=0
        ):
            raise ValueError("Actual native OI merge differs from exact raw top-of-hour values")
        for index, row in actual.iterrows():
            if index < 20:
                continue
            window = amount.iloc[index - 5 : index + 1]
            known = window.notna().all() and (window > 0).all()
            rising = bool(known and window.iloc[-1] > window.iloc[0])
            up = row.close > max(actual.high.iloc[index - 20 : index])
            down = row.close < min(actual.low.iloc[index - 20 : index])
            # Preserve explicit dropped-last-row unknown rather than fill it.
            known = bool(
                known
                and row.oi_window_known_1h is not None
                and pd.notna(row.oi_window_known_1h)
                and row.oi_window_known_1h
            )
            admitted = True if candidate["name"] == "PriceBreakoutFull" else known
            if candidate["name"] == "OIConfirmedBreakout":
                admitted &= rising
            if int(row.get("enter_long", 0) or 0) != int(up and admitted) or int(
                row.get("enter_short", 0) or 0
            ) != int(down and admitted):
                raise ValueError("Native source signals differ from independent raw formula")
        rows.append(
            {
                "strategy": candidate["name"],
                "native_indicator_rows": len(native),
                "native_reported_numeric_oi_rows": int(finite.sum()),
                "native_positive_oi_rows": int(actual.open_interest_amount_1h.gt(0).sum()),
                "native_known_oi_window_rows": int(actual.oi_window_known_1h.fillna(False).sum()),
                "independently_checked_signal_rows": len(actual) - 20,
                "entry_long": int(actual.enter_long.sum()),
                "entry_short": int(actual.enter_short.sum()),
            }
        )
        exchange.close()
    save(
        HERE / "preflight.json",
        {
            "passed": True,
            "actual_native_data_provider": True,
            "native_package": freqtrade.__file__,
            "rows": rows,
            "plan_sha256": sha(HERE / "plan.json"),
            "source_scope": "Fixed reference and two matched controls; no performance/causality certification.",
        },
    )
    print(json.dumps({"passed": True, "actual_native_indicator_signal_checks": rows}))


if __name__ == "__main__":
    main()
