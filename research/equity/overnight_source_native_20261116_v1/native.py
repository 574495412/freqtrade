"""Real native historical loading, StrategyResolver and Registry calculations."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from bootstrap import HERE, ROOT, boot, save, sha


def require_freeze():
    for path, digest in json.loads((HERE / "implementation_freeze.json").read_text()).items():
        if sha(path) != digest:
            raise ValueError(f"Frozen implementation drift: {path}")


def main():
    require_freeze()
    owner = boot()
    from RegistrySignal import RegistryFeatures
    from src.factors.registry import Registry

    from freqtrade.data.equity_provider import EquityDataProvider, EquitySchedule
    from freqtrade.data.history.equity import EquityAdmissionError, EquitySessionContract
    from freqtrade.enums import CandleType, RunMode
    from freqtrade.optimize.equity_backtesting import EquityBacktesting
    from freqtrade.resolvers import StrategyResolver

    ref = json.loads((HERE / "input_reference.json").read_text())
    if sha(ref["inputs"]) != ref["inputs_sha256"]:
        raise ValueError("Parent input reference changed")
    inputs = json.loads(Path(ref["inputs"]).read_text())
    schedules, declared = {}, {}
    for market, rec in inputs["schedules"].items():
        if sha(rec["path"]) != rec["sha256"]:
            raise ValueError("Session schedule drift")
        frame = pd.read_csv(rec["path"], index_col=0)
        frame.index = pd.to_datetime(frame.index, utc=True)
        schedules[market] = frame
    hashes = {}
    for rec in inputs["records"]:
        for field, expected in [("source", "source_sha256"), ("staged", "staged_sha256")]:
            if sha(rec[field]) != rec[expected]:
                raise ValueError(f"Original input drift: {rec[field]}")
            hashes[rec[field]] = rec[expected]
        contract = EquitySessionContract(
            rec["pair"],
            rec["calendar"],
            tuple(schedules[rec["calendar"]].index.strftime("%Y-%m-%d")),
        )
        declared[rec["pair"]] = EquitySchedule(contract, schedules[rec["calendar"]])
    for path in Path(ref["native_history"]).rglob("*.feather"):
        hashes[str(path)] = sha(path)
    save(HERE / "input_hashes.json", hashes)
    config = {
        "runmode": RunMode.BACKTEST,
        "timeframe": "1d",
        "datadir": Path(ref["native_history"]),
        "candle_type_def": CandleType.SPOT,
        "equity_diagnostic_only": True,
        "strategy": "RegistrySignal",
        "strategy_path": str(HERE),
        "user_data_dir": ROOT / "user_data",
        "trading_mode": "spot",
        "stake_currency": "USD",
        "stake_amount": 100.0,
    }
    provider = EquityDataProvider(config, declared)
    strategy = StrategyResolver.load_strategy(config)
    strategy.dp = provider
    strategy.bot_start()
    rows, cohorts = [], {"XNYS": [], "XHKG": []}
    for rec in inputs["records"]:
        try:
            frame = provider.historic_ohlcv(rec["pair"], "1d")
        except EquityAdmissionError as exc:
            rows.append(
                {
                    "pair": rec["pair"],
                    "market": rec["calendar"],
                    "status": "rejected",
                    "reason": str(exc),
                }
            )
            continue
        pd.testing.assert_frame_equal(
            frame, pd.read_parquet(rec["staged"]), check_exact=True, check_dtype=False
        )
        cohorts[rec["calendar"]].append(rec["pair"])
        rows.append(
            {
                "pair": rec["pair"],
                "market": rec["calendar"],
                "status": "admitted",
                "rows": len(frame),
            }
        )
    protocol = json.loads((HERE / "protocol.json").read_text())
    registry = Registry(HERE / "zoo")
    prefix_cells = boundary_checks = score_cells = 0
    outcomes = []
    for market, pairs in cohorts.items():
        factors = [k for k, v in protocol["factors"].items() if market in v["markets"]]
        bridge = RegistryFeatures(
            registry, provider, pairs, factors, "equity_us" if market == "XNYS" else "equity_hk"
        )
        strategy.registry_features = bridge
        folder = HERE / "native" / market
        folder.mkdir(parents=True, exist_ok=False)
        for field, panel in bridge.panel.items():
            panel.to_parquet(folder / f"{field}.parquet")
        for identity, scores in bridge.scores.items():
            scores.to_parquet(folder / f"{identity}.parquet")
            score_cells += int(np.isfinite(scores.to_numpy()).sum())
        # Actual Registry refits, including all peers. No sliced full-history score
        # is passed off as an independently recomputed historical prediction.
        for cutoff in ["2025-03-03", "2025-12-01", "2026-06-01"]:
            before = bridge.dates < pd.Timestamp(cutoff, tz="UTC")
            changed = {k: v.copy() for k, v in bridge.panel.items()}
            for field, panel in changed.items():
                panel.loc[~before] *= 7.0 if field == "volume" else 1e5
            for identity, scores in bridge.scores.items():
                perturbed = registry.compute(identity, changed)
                truncated = registry.compute(
                    identity, {k: v.loc[before] for k, v in bridge.panel.items()}
                )
                pd.testing.assert_frame_equal(
                    scores.loc[before], perturbed.loc[before], check_exact=True
                )
                pd.testing.assert_frame_equal(scores.loc[before], truncated, check_exact=True)
                prefix_cells += int(before.sum()) * len(pairs) * 2
        for pair in pairs:
            frame = provider.historic_ohlcv(pair, "1d")
            advised = strategy.advise_indicators(frame, {"pair": pair})
            provider._set_cached_df(pair, "1d", advised, CandleType.SPOT)
            schedule = provider.schedules[pair].frame
            for i in [0, len(frame) // 2, len(frame) - 1]:
                for at, count in [
                    (schedule.open.iloc[i], i),
                    (schedule.close.iloc[i] - pd.Timedelta(nanoseconds=1), i),
                    (schedule.close.iloc[i], i + 1),
                ]:
                    provider._set_dataframe_max_date(at)
                    for identity in factors:
                        visible = bridge.visible(identity, pair)
                        if len(visible) != count:
                            raise ValueError("Current/future daily score leaked to callback")
                        cached, _ = provider.get_analyzed_dataframe(pair, "1d")
                        np.testing.assert_array_equal(cached[identity], visible.score.iloc[-1000:])
                        boundary_checks += 1
        outcomes.append({"market": market, "admitted": len(pairs), "factors": factors})
        print("NATIVE", market, len(pairs), factors, flush=True)
    try:
        EquityBacktesting({})
    except EquityAdmissionError as exc:
        financial_guard = str(exc)
    else:
        raise AssertionError("Incomplete historical equity account unexpectedly admitted")
    save(
        HERE / "native_result.json",
        {
            "owner": owner,
            "records": rows,
            "cohorts": outcomes,
            "finite_score_cells": score_cells,
            "actual_prefix_cells": prefix_cells,
            "callback_boundary_checks": boundary_checks,
            "full_registry_computations": sum(len(r["factors"]) for r in outcomes),
            "additional_prefix_registry_computations": sum(len(r["factors"]) for r in outcomes) * 6,
            "ordinary_account_guard": financial_guard,
            "complete_original_pool_admitted": False,
            "historical_accounts": 0,
            "bench_records": 0,
        },
    )
    print(
        "NATIVE DONE",
        score_cells,
        "scores;",
        prefix_cells,
        "prefix cells;",
        boundary_checks,
        "clock checks; 0 accounts",
        flush=True,
    )


if __name__ == "__main__":
    main()
