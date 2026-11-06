"""Frozen breadth-conditioned laggard diffusion, on the existing 5m account model."""

import os
import sys
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.trend_mainline.account import metrics, replay
from research.crypto.trend_mainline.data import load, prepare


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/breadth_diffusion_20261107_v001"
PLAN = HERE / "registration.json"
SOURCES = (
    Path(__file__),
    ROOT / "freqtrade/research/data.py",
    HERE.parent / "trend_mainline/data.py",
    HERE.parent / "trend_mainline/account.py",
)
CASES = ("BreadthDiffusion", "LocalConfirmation")


def register():
    if PLAN.exists() or OUT.exists():
        raise FileExistsError("Registration already exists")
    market = ROOT / "user_data/crypto_quant/nfi40_data_v002_klines"
    funding = ROOT / "user_data/crypto_quant/nfi40_data_v002"
    save(
        PLAN,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "symbols": sorted(
                [
                    "ADAUSDT",
                    "AVAXUSDT",
                    "BNBUSDT",
                    "DOGEUSDT",
                    "DOTUSDT",
                    "ETHUSDT",
                    "LINKUSDT",
                    "LTCUSDT",
                    "SOLUSDT",
                    "XRPUSDT",
                ]
            ),
            "universe_basis": (
                "Ten established liquid altcoin perpetuals drawn from the original fixed wide X8 "
                "list; selected for liquidity and complete schedule before viewing candidate "
                "outcomes. Survivorship remains."
            ),
            "market_dataset": str(market),
            "funding_dataset": str(funding),
            "market_receipts_sha256": sha(market / "receipts.json"),
            "funding_receipts_sha256": sha(funding / "receipts.json"),
            "source_hashes": {str(p.relative_to(ROOT)): sha(p) for p in SOURCES},
            "hypothesis": (
                "A laggard that starts moving in the direction of a broad prior peer impulse may "
                "continue closing the gap over the next 12 hours."
            ),
            "cases": list(CASES),
            "data_start": "2023-10-01",
            "start": "2024-01-01",
            "validation_start": "2025-01-01",
            "holdout_start": "2026-01-01",
            "end": "2026-09-01",
            "capital_usdt": 1000,
            "fee_per_side": 0.0005,
            "extra_cost_bps_per_side": [3, 10],
            "liquidity": {"top_n": 10, "minimum_daily_quote_usdt": 50000000},
            "signal": {
                "local_1h_min_abs": 0.0035,
                "local_4h_max_abs": 0.01,
                "minimum_aligned_other_contracts": 7,
                "peer_4h_median_min_abs": 0.01,
                "pulse_hours": 12,
                "feature_buffer_minutes": 5,
                "clock_policy": (
                    "Use close known at t-5m, prior completed daily features and prior 60m "
                    "turnover ending t-5m. Signal only on a new eligible false-to-true edge. Hold "
                    "signal for 12h; same symbol ignores new pulses during that interval."
                ),
                "control": (
                    "Identical local entry, holding, sizing and execution. Omit only the leave- "
                    "one-out peer breadth and median gate."
                ),
            },
            "risk": {
                "risk_per_position": 0.02,
                "maximum_position_fraction": 0.45,
                "maximum_gross_fraction": 0.9,
                "maximum_positions": 2,
                "leverage": 1,
                "account_drawdown_halt": 0.35,
            },
            "execution": {
                "prior_hour_participation": 0.01,
                "realized_interval_participation": 0.01,
                "policy": (
                    "Existing account engine. Trade at next five-minute close with adverse costs "
                    "and volume cap. Permanent 35% observed drawdown halt, fixed two-daily-ATR "
                    "stop checked on completed bars, 24h reentry cooldown, alphabetical "
                    "admission, hourly signal expiry, no DCA. Funding uses sealed 8h schedule and "
                    "hourly mark-open."
                ),
            },
            "decision": {
                "cash_control": "constant 1000 USDT",
                "pass": (
                    "Both cost cases have at least 30 natural closes, positive full net return "
                    "greater than local-only control, max drawdown <=35%, no risk halt, positive "
                    "paired net excess in both 2025 and Jan-Aug 2026 continuous slices."
                ),
                "promotion": (
                    "A historical screening pass only. Independent future data and execution "
                    "evidence required before paper deployment."
                ),
                "frozen_trials": 4,
            },
            "known_history": (
                "Periods are retrospective. Prior breadth20/crowding features used six contracts "
                "and 20m context, without this 4h leave-one-out diffusion account. No parameter "
                "sweep or post-result threshold change."
            ),
            "orders_enabled": False,
        },
    )


def signals(frames, plan, case):
    config = plan["signal"]
    buffered = pd.DataFrame(
        {s: row["bars"].close.shift(2).asfreq("h") for s, row in frames.items()}
    )
    r1 = buffered.pct_change(fill_method=None)
    r4 = buffered.pct_change(4, fill_method=None)
    result = {}
    for symbol, item in frames.items():
        f = item["features"].copy()
        # At midnight the most recent UTC daily close still falls inside the buffer.
        first_hour = f.index.hour == 0
        f["eligible"] = f["eligible"].mask(first_hour, f["eligible"].shift(1, fill_value=False))
        for column in ("atr", "high55", "low55"):
            f[column] = f[column].mask(first_hour, f[column].shift(1))
        f["previous_close"] = buffered[symbol]
        f["prior_hour_quote"] = item["bars"].quote_volume.rolling(12).sum().shift(2).asfreq("h")
        peers = r4.drop(columns=symbol)
        candidate = pd.Series(0, index=f.index)
        for side in (1, -1):
            active = (
                f["eligible"]
                & (side * r1[symbol] >= config["local_1h_min_abs"])
                & (r4[symbol].abs() <= config["local_4h_max_abs"])
            )
            if case == "BreadthDiffusion":
                aligned = side * peers
                active &= (
                    aligned.notna().all(axis=1)
                    & (aligned.gt(0).sum(axis=1) >= config["minimum_aligned_other_contracts"])
                    & (aligned.median(axis=1) >= config["peer_4h_median_min_abs"])
                )
            candidate.loc[active & ~active.shift(1, fill_value=False)] = side
        pulse = candidate.to_numpy().copy()
        until = 0
        for i in np.flatnonzero(pulse):
            if i >= until:
                until = i + config["pulse_hours"]
                candidate.iloc[i:until] = pulse[i]
            else:
                candidate.iloc[i] = candidate.iloc[i - 1]
        f["momentum"] = candidate
        result[symbol] = f
    return result


def window_returns(curve, plan):
    nav = curve.set_index("date").equity
    keys = ("start", "validation_start", "holdout_start", "end")
    output = {}
    for left, right in pairwise(keys):
        lo = pd.Timestamp(plan[left], tz="UTC")
        hi = pd.Timestamp(plan[right], tz="UTC")
        path = nav.loc[lo:hi]
        output[plan[left]] = {
            "net_return": float(path.iloc[-1] / path.iloc[0] - 1),
            "drawdown": float((1 - path / path.cummax()).max()),
        }
    return output


def verify_prefix(frames, plan, case, full):
    end = pd.Timestamp("2025-07-01", tz="UTC")
    short = {
        s: {
            "bars": p["bars"].loc[p["bars"].index < end],
            "features": p["features"].loc[p["features"].index < end],
        }
        for s, p in frames.items()
    }
    for s, truncated in signals(short, plan, case).items():
        pd.testing.assert_series_equal(truncated.momentum, full[s].momentum.loc[truncated.index])


def run():
    if any(k.startswith("FREQTRADE__") for k in os.environ):
        raise ValueError("Unexpected environment override")
    plan = read(PLAN)
    for path, checksum in plan["source_hashes"].items():
        if sha(ROOT / path) != checksum:
            raise ValueError(f"Registered source changed: {path}")
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "attempt_003.json").exists():
        raise FileExistsError("Corrected attempt already started")
    save(
        OUT / "attempt_003.json",
        {
            "started_at_utc": datetime.now(UTC).isoformat(),
            "registration_sha256": sha(PLAN),
            "attempted_accounts": 4,
        },
    )
    if not (OUT / "data/receipts.json").is_file():
        prepare(plan, OUT / "data")
    frames = load(OUT / "data", plan)
    result = {}
    for case in CASES:
        features = signals(frames, plan, case)
        verify_prefix(frames, plan, case, features)
        inputs = {s: {**frames[s], "features": f} for s, f in features.items()}
        pd.DataFrame({s: f.momentum for s, f in features.items()}).to_parquet(
            OUT / f"{case}-signals.parquet"
        )
        for slip in plan["extra_cost_bps_per_side"]:
            account = replay(
                inputs, plan, "Momentum63", plan["capital_usdt"], slip, plan["start"], plan["end"]
            )
            key = f"{case}_{5 + slip}bps"
            directory = OUT / key
            directory.mkdir()
            account["curve"].to_parquet(directory / "nav.parquet", index=False)
            for name in ("events", "decisions", "trades", "state"):
                save(directory / f"{name}.json", account[name])
            summary = metrics(account, plan["capital_usdt"])
            summary.update(
                periods=window_returns(account["curve"], plan),
                prefix_test=True,
                cost_bps_per_side=5 + slip,
            )
            result[key] = summary
            print(
                key,
                summary["net_return"],
                summary["max_drawdown"],
                summary["closed_trades"],
                flush=True,
            )
    failures = {}
    for cost in (8, 15):
        trial = result[f"BreadthDiffusion_{cost}bps"]
        control = result[f"LocalConfirmation_{cost}bps"]
        failures[cost] = [
            name
            for name, passes in {
                "at_least_30_closes": trial["closed_trades"] >= 30,
                "positive_net_return": trial["net_return"] > 0,
                "beats_local_control": trial["net_return"] > control["net_return"],
                "max_drawdown_at_most_35pct": trial["max_drawdown"] <= 0.35,
                "no_account_halt": not trial["account_halted"],
                "validation_excess_positive": trial["periods"][plan["validation_start"]][
                    "net_return"
                ]
                > control["periods"][plan["validation_start"]]["net_return"],
                "holdout_excess_positive": trial["periods"][plan["holdout_start"]]["net_return"]
                > control["periods"][plan["holdout_start"]]["net_return"],
            }.items()
            if not passes
        ]
    save(
        HERE / "result.json",
        {
            "registration_sha256": sha(PLAN),
            "cost_results": result,
            "failed_gates": failures,
            "screen_pass": not any(failures.values()),
            "completed_accounts": 4,
            "cash_control_net_return": 0,
            "orders_enabled": False,
            "execution_tier": (
                "retrospective 5m capacity-constrained modeled account, independent OOS absent"
            ),
        },
    )


if __name__ == "__main__":
    if sys.argv[1:] == ["register"]:
        register()
    elif sys.argv[1:] == ["run"]:
        run()
    else:
        raise SystemExit("Use register or run")
