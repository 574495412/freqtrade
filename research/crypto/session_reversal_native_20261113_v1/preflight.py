"""Native strategy/source-formula, input calendar and causal-prefix checks."""

import ast
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save

HERE = Path(__file__).resolve().parent


def require(value, reason):
    if not value:
        raise ValueError(reason)


def source_functions():
    tree = ast.parse((HERE / "source/btc_eth_backtest.py").read_text())
    nodes = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name in ["build_day_night_sessions", "position_array"]
    ]
    require(len(nodes) == 2, "Exact source pure functions missing")
    namespace = {"np": np, "pd": pd}
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), "pinned_source_pure_functions", "exec"),
        namespace,
    )  # noqa: S102
    return namespace


def main():
    plan = json.loads((HERE / "plan.json").read_text())
    spec = importlib.util.spec_from_file_location("session_runner", HERE / "native_runner.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    for candidate in plan["candidates"]:
        runner.verify(plan, candidate)
    sys.path.insert(0, plan["engine_source"])
    from freqtrade.resolvers import StrategyResolver

    source = source_functions()
    records = []
    for candidate in plan["candidates"]:
        sym = candidate["pairs"][0].split("/")[0]
        pair = candidate["pairs"][0]
        config = json.loads(Path(candidate["config"]["path"]).read_text())
        config["strategy_path"] = str(HERE)
        owner = StrategyResolver.load_strategy(config)
        file = Path(plan["datadir"]) / f"futures/{sym}_USDT_USDT-5m-futures.feather"
        prices = pd.read_feather(file)
        prices = prices.loc[
            prices.date.ge(pd.Timestamp("2024-12-28T00:00Z"))
            & prices.date.lt(pd.Timestamp(plan["end"]))
        ].reset_index(drop=True)
        grid = pd.date_range("2024-12-28T00:00Z", plan["end"], freq="5min", inclusive="left")
        require(
            pd.DatetimeIndex(prices.date).as_unit("ns").equals(grid.as_unit("ns")),
            "Missing/duplicate5m data",
        )
        require(
            np.isfinite(prices[["open", "high", "low", "close"]]).all().all()
            and prices.close.gt(0).all(),
            "Invalid price",
        )
        hourly = pd.read_feather(
            Path(plan["datadir"]) / f"futures/{sym}_USDT_USDT-1h-futures.feather"
        )
        hourly = hourly.loc[
            hourly.date.ge(grid[0]) & hourly.date.lt(pd.Timestamp(plan["end"]))
        ].copy()
        native_prices = prices.set_index("date").close
        for row in hourly.itertuples():
            require(
                abs(float(native_prices.loc[row.date + pd.Timedelta(minutes=55)]) - row.close)
                < 1e-8,
                "Native5m/hourlyclose conflict",
            )
        hour = 8 if sym == "BTC" else 5
        source_prices = hourly.set_index("date")
        source_prices.index.name = "timestamp"
        sessions = source["build_day_night_sessions"](source_prices, hour).set_index("trading_date")

        def calculate(frame):
            return owner.advise_entry(
                owner.advise_indicators(frame.copy(), {"pair": pair}), {"pair": pair}
            )

        actual = calculate(prices)
        actual.to_parquet(
            Path(plan["output_root"]) / f"preflight_{candidate['name']}.parquet", index=False
        )
        selected = actual.loc[actual.session_boundary & actual.date.ge(pd.Timestamp(plan["start"]))]
        for row in selected.itertuples():
            day = row.date.normalize()
            is_day = row.date.hour == hour
            previous_source_date = day - pd.Timedelta(days=1) if is_day else day
            field = "day_ret" if is_day else "night_ret"
            lagged = float(sessions.loc[previous_source_date, field])
            require(abs(row.lag_same_session_return - lagged) < 1e-11, "Pinned source lag differs")
            rule = "reversal" if sym == "BTC" or is_day else "long"
            if candidate["strategy"] != "SourceSessions":
                rule = "long"
            target = source["position_array"](rule, pd.Series([lagged]))[0]
            require(
                row.session_known and row.session_target == target,
                "Native target differs from source function",
            )
            require(
                row.enter_long == (target == 1) and row.enter_short == (target == -1),
                "Native signal differs",
            )
        checks = []
        fields = [
            "lag_same_session_return",
            "session_known",
            "session_target",
            "enter_long",
            "enter_short",
        ]
        for edge in ["2025-07-01T00:00Z", "2026-01-01T00:00Z", "2026-08-01T00:00Z"]:
            mask = prices.date.le(pd.Timestamp(edge))
            prefix = calculate(prices.loc[mask])
            changed = prices.copy()
            changed.loc[~mask, ["open", "high", "low", "close"]] *= 3
            perturbed = calculate(changed)
            pd.testing.assert_frame_equal(actual.loc[mask, fields], prefix[fields])
            pd.testing.assert_frame_equal(actual.loc[mask, fields], perturbed.loc[mask, fields])
            checks.append({"edge": edge, "prefix_and_future_price_perturbation_exact": True})
        records.append(
            {
                "candidate": candidate["name"],
                "native_rows": len(actual),
                "actual_source_boundary_checks": len(selected),
                "hourly_close_matches": len(hourly),
                "causal_checks": checks,
            }
        )
    save(
        HERE / "preflight.json",
        {
            "passed": True,
            "records": records,
            "native_strategy_interface": True,
            "source_pure_functions_not_account_engine": True,
            "independent_oos_certified": False,
        },
    )
    print(
        json.dumps(
            {
                "preflight_passed": True,
                "candidates": len(records),
                "source_boundary_checks": sum(r["actual_source_boundary_checks"] for r in records),
            }
        )
    )


if __name__ == "__main__":
    main()
