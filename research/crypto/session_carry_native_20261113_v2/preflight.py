"""Actual native strategies, exact source signs, clocks and future-prefix checks."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import io
import numpy as np
import pandas as pd

from research.crypto.session_reversal_native_20261113_v1.preflight_v2 import source_functions
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / "session_reversal_native_20261113_v1"


def require(value, reason):
    if not value:
        raise ValueError(reason)


def funding(plan):
    prior = json.loads((OLD / "funding_audit.json").read_text())
    results = []
    for record in prior["records"]:
        frames = []
        for receipt in record["archives"]:
            archive_path = Path(receipt["path"])
            require(sha(archive_path) == receipt["sha256"], "Funding ZIP drift")
            require(
                sha(archive_path.with_suffix(".CHECKSUM")) == receipt["checksum_sha256"],
                "Official checksum drift",
            )
            require(
                sha(archive_path) == archive_path.with_suffix(".CHECKSUM").read_text().split()[0],
                "Official funding SHA mismatch",
            )
            with ZipFile(archive_path) as archive:
                frames.append(pd.read_csv(io.BytesIO(archive.read(archive.namelist()[0]))))
        full = pd.concat(frames, ignore_index=True)
        dates = pd.to_datetime(full.calc_time, unit="ms", utc=True).dt.floor("h")
        expected = pd.date_range(plan["start"], plan["end"], freq="8h", inclusive="left")
        require(
            pd.DatetimeIndex(dates).as_unit("ns").equals(expected.as_unit("ns")),
            "Funding event grid",
        )
        require(full.funding_interval_hours.eq(8).all(), "Funding interval changed")
        native = pd.read_feather(
            Path(plan["datadir"]) / f"futures/{record['symbol']}_USDT_USDT-1h-funding_rate.feather"
        )
        native = native.loc[native.date.ge(expected[0]) & native.date.le(expected[-1])]
        require(
            pd.DatetimeIndex(native.date).as_unit("ns").equals(expected.as_unit("ns")),
            "Native funding grid",
        )
        require(
            np.allclose(native.funding_rate, full.last_funding_rate, rtol=0, atol=1e-12),
            "Funding rate mismatch",
        )
        results.append(
            {"symbol": record["symbol"], "events": len(full), "archives_rechecked": len(frames)}
        )
    save(
        HERE / "funding_recheck.json",
        {
            "passed": True,
            "records": results,
            "new_network_data": False,
            "actual_account_payments_certified": False,
        },
    )


def main():
    plan = json.loads((HERE / "plan.json").read_text())
    spec = importlib.util.spec_from_file_location("carry_runner", HERE / "native_runner.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    for candidate in plan["candidates"]:
        runner.verify(plan, candidate)
    funding(plan)
    sys.path.insert(0, plan["engine_source"])
    from freqtrade.optimize import backtesting as native
    from freqtrade.resolvers import StrategyResolver

    source = source_functions()
    records = []
    for candidate in plan["candidates"]:
        config = json.loads(Path(candidate["config"]["path"]).read_text())
        config.update(strategy_path=str(HERE), user_data_dir=Path(plan["output_root"]))
        owner = StrategyResolver.load_strategy(config)
        pair = candidate["pairs"][0]
        sym = pair.split("/")[0]
        prices = pd.read_feather(
            Path(plan["datadir"]) / f"futures/{sym}_USDT_USDT-5m-futures.feather"
        )
        prices = prices.loc[
            prices.date.ge(pd.Timestamp("2024-12-28T00:00Z"))
            & prices.date.lt(pd.Timestamp(plan["end"]))
        ].reset_index(drop=True)
        grid = pd.date_range("2024-12-28T00:00Z", plan["end"], freq="5min", inclusive="left")
        require(
            pd.DatetimeIndex(prices.date).as_unit("ns").equals(grid.as_unit("ns")),
            "Incomplete raw5m grid",
        )
        require(
            np.isfinite(prices[["open", "high", "low", "close"]]).all().all()
            and prices.close.gt(0).all(),
            "Invalid quote",
        )
        hourly = pd.read_feather(
            Path(plan["datadir"]) / f"futures/{sym}_USDT_USDT-1h-futures.feather"
        )
        hourly = hourly.loc[
            hourly.date.ge(grid[0]) & hourly.date.lt(pd.Timestamp(plan["end"]))
        ].copy()
        last5 = (
            prices.set_index("date")
            .close.reindex(pd.DatetimeIndex(hourly.date) + pd.Timedelta(minutes=55))
            .to_numpy()
        )
        require(np.array_equal(last5, hourly.close.to_numpy()), "5m/hourly endpoint mismatch")
        hourly = hourly.set_index("date")
        hourly.index.name = "timestamp"
        hour = 8 if sym == "BTC" else 5
        sessions = source["build_day_night_sessions"](hourly, hour).set_index("trading_date")

        def calculate(frame):
            indicators = owner.advise_indicators(frame.copy(), {"pair": pair})
            return owner.advise_exit(owner.advise_entry(indicators, {"pair": pair}), {"pair": pair})

        actual = calculate(prices)
        actual.to_parquet(
            Path(plan["output_root"]) / f"preflight_{candidate['name']}.parquet", index=False
        )
        carry = candidate["role"] != "default_off_parity"
        boundary_field = "next_session_boundary" if carry else "session_boundary"
        target_field = "next_session_target" if carry else "session_target"
        lag_field = "next_session_lag_return" if carry else "lag_same_session_return"
        selected = actual.loc[
            actual[boundary_field]
            & actual.date.ge(pd.Timestamp(plan["start"]) - pd.Timedelta(minutes=5))
        ]
        for row in selected.itertuples():
            clock = row.date + pd.Timedelta(minutes=5) if carry else row.date
            is_day = clock.hour == hour
            prior = clock.normalize() - pd.Timedelta(days=int(is_day))
            lagged = float(sessions.loc[prior, "day_ret" if is_day else "night_ret"])
            require(abs(getattr(row, lag_field) - lagged) < 1e-11, "Source lag differs")
            rule = "reversal" if sym == "BTC" or is_day else "long"
            if candidate["strategy"] in ["BoundaryCarryLong", "PassiveLong"]:
                rule = "long"
            target = source["position_array"](rule, pd.Series([lagged]))[0]
            require(getattr(row, target_field) == target, "Source sign differs")
            if carry:
                require(
                    row.exit_long == (target != 1) and row.exit_short == (target != -1),
                    "Exit collision policy",
                )
                if candidate["strategy"] != "ClockMatchedCarrySource":
                    data = [0] * (
                        max(native.LONG_IDX, native.ELONG_IDX, native.SHORT_IDX, native.ESHORT_IDX)
                        + 1
                    )
                    for idx, value in [
                        (native.LONG_IDX, row.enter_long),
                        (native.ELONG_IDX, row.exit_long),
                        (native.SHORT_IDX, row.enter_short),
                        (native.ESHORT_IDX, row.exit_short),
                    ]:
                        data[idx] = value
                    classified = native.Backtesting.check_for_trade_entry(
                        SimpleNamespace(_can_short=True), data
                    )
                    require(
                        classified
                        == ("long" if target == 1 else "short" if target == -1 else None),
                        "Native collision classifier differs",
                    )
        fields = ["enter_long", "enter_short", "exit_long", "exit_short"]
        if carry:
            fields += ["next_session_lag_return", "next_session_known", "next_session_target"]
        checks = []
        for edge in ["2025-07-01T07:55Z", "2026-01-01T04:55Z", "2026-08-01T19:55Z"]:
            mask = prices.date.le(pd.Timestamp(edge))
            prefix = calculate(prices.loc[mask])
            changed = prices.copy()
            changed.loc[~mask, ["open", "high", "low", "close"]] *= 3
            perturbed = calculate(changed)
            pd.testing.assert_frame_equal(actual.loc[mask, fields], prefix[fields])
            pd.testing.assert_frame_equal(actual.loc[mask, fields], perturbed.loc[mask, fields])
            checks.append({"edge": edge, "prefix_and_future_perturbation_exact": True})
        if carry:
            broken = prices.copy()
            broken.loc[broken.date.eq(pd.Timestamp("2025-06-30T07:55Z")), "close"] = np.nan
            missing = calculate(broken)
            blocked = missing.next_session_boundary & ~missing.next_session_known
            require(
                missing.loc[blocked, "next_session_target"].eq(0).all(),
                "Missing quote invents direction",
            )
            require(
                missing.loc[blocked, ["exit_long", "exit_short"]].eq(1).all().all(),
                "Missing quote does not close exposure",
            )
        records.append(
            {
                "candidate": candidate["name"],
                "source_boundary_checks": len(selected),
                "native_rows": len(actual),
                "causal_checks": checks,
                "native_collision_rules_checked": carry,
            }
        )
    save(
        HERE / "preflight.json",
        {
            "passed": True,
            "records": records,
            "source_functions_are_not_account_engine": True,
            "independent_oos_certified": False,
        },
    )
    print(
        json.dumps(
            {
                "passed": True,
                "candidates": len(records),
                "source_boundary_checks": sum(r["source_boundary_checks"] for r in records),
            }
        )
    )


if __name__ == "__main__":
    main()
