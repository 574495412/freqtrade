"""A03 directional breakout after persistent negative settled funding."""

import os
import sys
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.basis_flush_20261107.run import summarize
from research.crypto.trend_mainline.account import replay
from research.crypto.trend_mainline.data import load, prepare


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = ROOT / "user_data/crypto_quant"
OUT = DATA / "funding_breakout_20261107_v001"
PLAN = HERE / "registration.json"
MARKET = DATA / "nfi40_data_v002_klines"
FUNDING = DATA / "nfi40_data_v002"
CASES = ("RecentFunding", "PriceBreakout", "StaleFunding")
YEARS = ("2024-01-02", "2025-01-01", "2026-01-01", "2026-09-01")


def register():
    if PLAN.exists() or OUT.exists():
        raise FileExistsError("A03 already registered")
    symbols = sorted(read(MARKET / "plan.json")["symbols"])
    excluded = {
        "AXSUSDT": "variable funding cadence",
        "MASKUSDT": "variable funding cadence",
        "SKLUSDT": "variable funding cadence",
        "TRBUSDT": "variable funding cadence",
        "DYDXUSDT": "prior source lineage conflict in trade archives",
    }
    symbols = [s for s in symbols if s not in excluded]
    expected = pd.date_range("2024-01-01", YEARS[-1], freq="8h", tz="UTC", inclusive="left")
    for symbol in symbols:
        path = FUNDING / f"{symbol}_funding_events.parquet"
        clocks = pd.DatetimeIndex(pd.read_parquet(path, columns=["date"]).date).as_unit("us")
        actual = clocks[(clocks >= expected[0]) & (clocks <= expected[-1])]
        if not actual.equals(expected.as_unit("us")):
            raise ValueError(f"Funding schedule not complete: {symbol}")
    if len(symbols) != 35:
        raise ValueError(f"Unexpected frozen universe size: {len(symbols)}")
    sources = (
        Path(__file__),
        HERE / "verify.py",
        HERE.parent / "trend_mainline/account.py",
        HERE.parent / "trend_mainline/data.py",
        HERE.parent / "basis_flush_20261107/run.py",
        HERE.parent / "breadth_diffusion_20261107/verify.py",
        ROOT / "freqtrade/research/data.py",
    )
    save(
        PLAN,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "symbols": symbols,
            "excluded_contracts": excluded,
            "universe_policy": (
                "All members of the pre-existing fixed X8-wide market universe with an exact "
                "8h cadence, except the previously flagged archive conflict. Fixed incumbent "
                "universe has survivorship bias."
            ),
            "checksums": {str(path.relative_to(ROOT)): sha(path) for path in sources},
            "market_dataset": str(MARKET),
            "market_receipts_sha256": sha(MARKET / "receipts.json"),
            "funding_dataset": str(FUNDING),
            "funding_receipts_sha256": sha(FUNDING / "receipts.json"),
            "data_start": "2023-10-01",
            "funding_start": "2024-01-01",
            "start": YEARS[0],
            "split": YEARS[1],
            "late_split": YEARS[2],
            "end": YEARS[3],
            "cases": CASES,
            "hypothesis": (
                "A long breakout after persistent negative settled funding can capture a larger "
                "upward move than generic breakouts and breakouts filtered by stale funding."
            ),
            "signal": {
                "lookback_hours": 48,
                "breakout_buffer": 0.002,
                "funding_visibility_minutes": 15,
                "min_funding_sum": -0.0002,
                "stale_funding_days": 7,
                "pulse_hours": 36,
                "invalidation_below_level": 0.005,
                "definition": (
                    "Use buffered hourly perpetual closes known at decision minus five minutes. "
                    "Require close >= 1.002 times maximum of the prior 48 buffered hourly closes. "
                    "RecentFunding further needs 3 completed scheduled settlements with sum "
                    "<= -2bps, both of the two newest rates strictly negative. Use rates settled "
                    "by t-15m, a historical availability proxy. StaleFunding evaluates the "
                    "identical funding condition as of t-7d. PriceBreakout has no funding gate. "
                    "Signal starts at false-to-true edge, expires after 36 hours or when close "
                    "falls below 99.5% of initial breakout level. While active, enter when "
                    "capacity permits. Overlapping edges do not extend active signals."
                ),
            },
            "capital_usdt": 1000,
            "fee_per_side": 0.0005,
            "extra_cost_bps_per_side": [3, 10],
            "liquidity": {"top_n": 35, "minimum_daily_quote_usdt": 25_000_000},
            "risk": {
                "risk_per_position": 0.05,
                "maximum_position_fraction": 0.90,
                "maximum_gross_fraction": 0.90,
                "maximum_positions": 1,
                "leverage": 1,
                "account_drawdown_halt": 0.35,
            },
            "execution": {
                "prior_hour_participation": 0.01,
                "realized_interval_participation": 0.01,
                "policy": (
                    "Adverse next-five-minute close proxy, fee and funding charged, 2x daily ATR "
                    "delayed stop, 24h reentry cooldown, alphabetical competition for the single "
                    "slot and no averaging. At UTC midnight use the daily fields known before "
                    "the five-minute buffer. Funding cash uses hourly mark-open."
                ),
            },
            "decision": {
                "policy": (
                    "At both costs require positive full and each yearly slice return, full net "
                    "return greater than both controls, drawdown <=35% with no halt, at least 30 "
                    "closes in at least 12 UTC entry weeks. Separately record the 20% stress "
                    "CAGR hurdle. All slices are retrospective and part of one continuous NAV."
                ),
                "cash_control": "constant 1000 USDT",
                "paper_eligible": False,
                "production_eligible": False,
                "orders_enabled": False,
            },
        },
    )


def build_features(item, spec, case):
    f = item["features"].copy()
    bars = item["bars"]
    f["previous_close"] = bars.close.shift(2).reindex(f.index)
    f["prior_hour_quote"] = bars.quote_volume.rolling(12).sum().shift(2).reindex(f.index)
    midnight = f.index.hour == 0
    f["eligible"] = f.eligible.mask(midnight, f.eligible.shift(1, fill_value=False))
    for field in ("atr", "high55", "low55"):
        f[field] = f[field].mask(midnight, f[field].shift(1))
    close = f.previous_close
    level = close.shift(1).rolling(spec["lookback_hours"]).max()
    ready = f.eligible & (close >= level * (1 + spec["breakout_buffer"]))
    if case != "PriceBreakout":
        rates = item["funding"].funding_rate
        valid = (rates < 0) & (rates.shift(1) < 0)
        valid &= rates.rolling(3).sum() <= spec["min_funding_sum"]
        shift = pd.Timedelta(minutes=spec["funding_visibility_minutes"])
        if case == "StaleFunding":
            shift += pd.Timedelta(days=spec["stale_funding_days"])
        ready &= valid.reindex(f.index - shift, method="ffill", fill_value=False).to_numpy()
    pulse = np.zeros(len(f), dtype=np.int8)
    last = 0
    for edge in np.flatnonzero(ready & ~ready.shift(1, fill_value=False)):
        if edge >= last:
            last = min(edge + spec["pulse_hours"], len(f))
            floor = level.iloc[edge] * (1 - spec["invalidation_below_level"])
            invalidated = np.flatnonzero(close.iloc[edge:last].to_numpy() < floor)
            if len(invalidated):
                last = edge + invalidated[0]
            pulse[edge:last] = 1
    f["momentum"] = pulse
    return f


def persist(path, account):
    path.mkdir()
    account["curve"].to_parquet(path / "nav.parquet", index=False)
    for field in ("events", "decisions", "trades", "state"):
        save(path / f"{field}.json", account[field])


def score(plan, path):
    account = {field: read(path / f"{field}.json") for field in ("events", "trades", "state")}
    account["curve"] = pd.read_parquet(path / "nav.parquet")
    summary = summarize(account, plan)
    nav = account["curve"].set_index("date").equity
    boundaries = (plan[k] for k in ("start", "split", "late_split", "end"))
    summary["period_net_returns"] = {
        lo: float(nav.at[pd.Timestamp(hi, tz="UTC")] / nav.at[pd.Timestamp(lo, tz="UTC")] - 1)
        for lo, hi in pairwise(boundaries)
    }
    summary["active_nav_fraction"] = float(account["curve"].gross_exposure.gt(0).mean())
    return summary


def run():
    if any(k.startswith("FREQTRADE__") for k in os.environ):
        raise ValueError("Unexpected Freqtrade environment override")
    plan = read(PLAN)
    for path, digest in plan["checksums"].items():
        if sha(ROOT / path) != digest:
            raise ValueError(f"Registered source changed: {path}")
    OUT.mkdir()
    save(
        OUT / "attempt.json",
        {"registration_sha256": sha(PLAN), "started": datetime.now(UTC).isoformat()},
    )
    prepare({**plan, "start": plan["funding_start"]}, OUT / "data")
    panel = load(OUT / "data", plan)
    print(f"Admitted {len(panel)} instruments", flush=True)
    results = {}
    for case in plan["cases"]:
        inputs = {
            s: {**item, "features": build_features(item, plan["signal"], case)}
            for s, item in panel.items()
        }
        pd.DataFrame({s: item["features"].momentum for s, item in inputs.items()}).to_parquet(
            OUT / f"{case}-signals.parquet"
        )
        for slip in plan["extra_cost_bps_per_side"]:
            name = f"{case}_{5 + slip}bps"
            path = OUT / name
            account = replay(
                inputs, plan, "Momentum63", plan["capital_usdt"], slip, plan["start"], plan["end"]
            )
            persist(path, account)
            del account
            result = results[name] = score(plan, path)
            print(
                name,
                result["net_return"],
                result["max_drawdown"],
                result["closed_trades"],
                flush=True,
            )
    failed = {}
    for cost in (8, 15):
        candidate = results[f"RecentFunding_{cost}bps"]
        control = [results[f"{case}_{cost}bps"]["net_return"] for case in plan["cases"][1:]]
        checks = {
            "positive_full_excess": candidate["net_return"] > max(0, *control),
            "positive_each_slice": min(candidate["period_net_returns"].values()) > 0,
            "maximum_drawdown": candidate["max_drawdown"] <= 0.35
            and not candidate["account_halted"],
            "minimum_trades": candidate["closed_trades"] >= 30,
            "minimum_weeks": candidate["completed_position_entry_weeks"] >= 12,
        }
        failed[str(cost)] = [name for name, passed in checks.items() if not passed]
    save(
        HERE / "result.json",
        {
            "registration_sha256": sha(PLAN),
            "input_receipts_sha256": sha(OUT / "data/receipts.json"),
            "accounts": results,
            "failed_gates": failed,
            "historical_screen_passed": not any(failed.values()),
            "upside_screen_passed": results["RecentFunding_15bps"]["annualized_net_return"] >= 0.20,
            "paper_eligible": False,
        },
    )


if __name__ == "__main__":
    {"register": register, "run": run}[sys.argv[1]]()
