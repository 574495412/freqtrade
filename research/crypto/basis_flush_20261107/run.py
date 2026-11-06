"""Compare perpetual flush reversals with and without a spot-relative discount."""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.trend_mainline.account import metrics, replay


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PLAN = HERE / "registration.json"
DATA = ROOT / "user_data/crypto_quant"
SPOT = DATA / "spot_baseline_data_long_20261105_v001"
PERP = DATA / "breadth_diffusion_20261107_v001/data"
OUT = DATA / "basis_flush_20261107_v001"
CASES = ("BasisFlush", "PriceFlush")
SYMBOLS = ("ADA", "AVAX", "DOGE", "DOT", "ETH", "LINK", "LTC", "SOL", "XRP")


def register():
    if PLAN.exists() or OUT.exists():
        raise FileExistsError("Study already registered")
    sources = (
        Path(__file__),
        HERE / "verify.py",
        HERE.parent / "trend_mainline/account.py",
        HERE.parent / "breadth_diffusion_20261107/verify.py",
        ROOT / "freqtrade/research/data.py",
    )
    index_paths = (SPOT / "data-receipts.json", PERP / "receipts.json")
    checksums = {str(path.relative_to(ROOT)): sha(path) for path in (*sources, *index_paths)}
    for base, index_path in zip((SPOT / "candles", PERP), index_paths, strict=True):
        index = read(index_path)
        for asset in SYMBOLS:
            names = (
                [f"{asset}_USDT-5m.feather"]
                if base == SPOT / "candles"
                else [f"{asset}USDT_{kind}.parquet" for kind in ("5m", "features", "funding")]
            )
            for name in names:
                path = base / name
                if sha(path) != index[name]:
                    raise ValueError(f"Parent input differs: {path}")
                checksums[str(path.relative_to(ROOT))] = index[name]
    save(
        PLAN,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "technical_revision": {
                "prior_registration_sha256": sha(HERE / "registration_v001.json"),
                "first_attempt_ledger": "basis_flush_20261107_attempt_001",
                "reason": (
                    "First account wrote its ledger, then date-only Pandas indexing "
                    "returned a daily slice in summary. Use exact UTC boundary timestamps. "
                    "Signal, portfolio, costs and evaluation thresholds are identical."
                ),
            },
            "symbols": [f"{asset}USDT" for asset in SYMBOLS],
            "universe": (
                "Full overlap of the ten liquid A01 perpetuals and sealed spot cohort. Fixed "
                "incumbent universe has survivorship bias."
            ),
            "checksums": checksums,
            "warmup_start": "2024-10-01",
            "start": "2025-01-01",
            "split": "2026-01-01",
            "end": "2026-09-01",
            "history": (
                "Retrospective screen. Both chronological slices use a continuous account. Both "
                "were available before registration."
            ),
            "cases": CASES,
            "hypothesis": (
                "After a downside flush and partial stabilization, a large perpetual discount "
                "relative to its recent norm selects stronger long-perpetual rebounds."
            ),
            "signal": {
                "flush_bars": 6,
                "flush_return_at_most": -0.025,
                "basis_reference_bars": 2016,
                "basis_reference_lag_bars": 12,
                "relative_log_basis_at_most": -0.0025,
                "scan_bars": 12,
                "rebound_minimum": 0.005,
                "pulse_hours": 12,
                "information_buffer_minutes": 5,
                "liquidity": (
                    "Reuse 28-day mean quote turnover >= 50m USDT, with previous UTC day's "
                    "daily features used at midnight."
                ),
                "definition": (
                    "At each UTC hour evaluate the most recent 12 five-minute bars with closes "
                    "known at t-5m. Find the lowest close among qualifying 30m flushes, require "
                    "a 0.5% bounce from it. BasisFlush also requires the flush bar "
                    "log(perp/spot) <= a 7-day trailing median excluding the most recent hour "
                    "minus 25 bps. PriceFlush omits only that condition. Pulse begins at an "
                    "eligible false-to-true edge. Ignore overlapping new edges for each symbol "
                    "during the 12-hour pulse."
                ),
            },
            "capital_usdt": 1000,
            "fee_per_side": 0.0005,
            "extra_cost_bps_per_side": [3, 10],
            "risk": {
                "risk_per_position": 0.04,
                "maximum_position_fraction": 0.60,
                "maximum_gross_fraction": 0.95,
                "maximum_positions": 2,
                "leverage": 1,
                "account_drawdown_halt": 0.35,
            },
            "execution": {
                "prior_hour_participation": 0.01,
                "realized_interval_participation": 0.01,
                "policy": (
                    "Existing capacity model, adverse next 5m close, measured 8h funding rate "
                    "with hourly mark-open proxy, 2x daily ATR delayed stop, 24h reentry "
                    "cooldown, alphabetical admission, cash control and no position additions. "
                    "A 12h signal is not a minimum holding period. Gross admission bounds use "
                    "decision information; realized fills/marks can differ."
                ),
            },
            "decision": {
                "historical_screen": (
                    "At both costs require 30 completed positions across at least 12 distinct "
                    "UTC entry weeks, positive net return greater than cash and PriceFlush over "
                    "each chronological slice and the full window, drawdown <=35%, and no "
                    "drawdown halt."
                ),
                "upside_screen": (
                    "Report separately whether 15 bps account annualized return is at least "
                    "20%. This is a research hurdle; it does not establish the user's return "
                    "objective."
                ),
                "orders_enabled": False,
                "paper_eligible": False,
                "production_eligible": False,
            },
        },
    )


def admit(plan):
    for name, checksum in plan["checksums"].items():
        if sha(ROOT / name) != checksum:
            raise ValueError(f"Frozen input changed: {name}")
    panel = {}
    bars_index = pd.date_range(
        plan["warmup_start"], plan["end"], freq="5min", inclusive="left", tz="UTC"
    )
    fund_index = pd.date_range(plan["start"], plan["end"], freq="8h", inclusive="left", tz="UTC")
    for symbol in plan["symbols"]:
        asset = symbol.removesuffix("USDT")
        prices = pd.read_feather(SPOT / "candles" / f"{asset}_USDT-5m.feather").set_index("date")
        bars = pd.read_parquet(PERP / f"{symbol}_5m.parquet")
        prices = prices.loc[(prices.index >= bars_index[0]) & (prices.index <= bars_index[-1])]
        if not prices.index.equals(bars_index) or not bars.loc[bars_index].index.equals(bars_index):
            raise ValueError(f"Spot/perpetual time grid mismatch: {symbol}")
        if not np.isfinite(prices.close).all() or (prices.close <= 0).any():
            raise ValueError(f"Invalid spot close: {symbol}")
        funding = pd.read_parquet(PERP / f"{symbol}_funding.parquet")
        active_funding = funding.loc[fund_index[0] : fund_index[-1]]
        if not active_funding.index.equals(fund_index):
            raise ValueError(f"Funding time grid mismatch: {symbol}")
        panel[symbol] = {
            "bars": bars.loc[bars_index],
            "spot": prices.close,
            "features": pd.read_parquet(PERP / f"{symbol}_features.parquet").loc[bars_index[0] :],
            "funding": active_funding,
        }
    return panel


def signals(item, spec, case):
    close = item["bars"].close
    flush = close.pct_change(spec["flush_bars"], fill_method=None) <= spec["flush_return_at_most"]
    if case == "BasisFlush":
        basis = np.log(close / item["spot"])
        norm = (
            basis.shift(spec["basis_reference_lag_bars"])
            .rolling(spec["basis_reference_bars"])
            .median()
        )
        flush &= (basis - norm) <= spec["relative_log_basis_at_most"]
    trough = close.where(flush).rolling(spec["scan_bars"], min_periods=1).min()
    f = item["features"].copy()
    offset = 1 + spec["information_buffer_minutes"] // 5
    f["previous_close"] = close.shift(offset).reindex(f.index)
    f["prior_hour_quote"] = (
        item["bars"].quote_volume.rolling(12).sum().shift(offset).reindex(f.index)
    )
    first_hour = f.index.hour == 0
    f["eligible"] = f.eligible.mask(first_hour, f.eligible.shift(1, fill_value=False))
    for name in ("atr", "high55", "low55"):
        f[name] = f[name].mask(first_hour, f[name].shift(1))
    bounce = f.previous_close / trough.shift(offset).reindex(f.index) - 1
    ready = f.eligible & (bounce >= spec["rebound_minimum"])
    edges = np.flatnonzero(ready & ~ready.shift(1, fill_value=False))
    momentum = np.zeros(len(f), dtype=np.int8)
    until = 0
    for i in edges:
        if i >= until:
            until = i + spec["pulse_hours"]
            momentum[i:until] = 1
    f["momentum"] = momentum
    return f


def summarize(account, plan):
    summary = metrics(account, plan["capital_usdt"])
    nav = account["curve"].set_index("date").equity
    days = (nav.index[-1] - nav.index[0]).days
    summary["annualized_net_return"] = float((nav.iloc[-1] / nav.iloc[0]) ** (365 / days) - 1)
    summary["period_net_returns"] = {
        start: float(
            nav.at[pd.Timestamp(end, tz="UTC")] / nav.at[pd.Timestamp(start, tz="UTC")] - 1
        )
        for start, end in ((plan["start"], plan["split"]), (plan["split"], plan["end"]))
    }
    closed = {t["id"] for t in account["trades"]}
    entries = [
        pd.Timestamp(e["time"])
        for e in account["events"]
        if e["type"] == "entry" and e["position_id"] in closed
    ]
    summary["completed_position_entry_weeks"] = len(
        {(t.isocalendar().year, t.isocalendar().week) for t in entries}
    )
    return summary


def run():
    if any(key.startswith("FREQTRADE__") for key in os.environ):
        raise ValueError("Unexpected Freqtrade config override")
    plan = read(PLAN)
    panel = admit(plan)
    OUT.mkdir()
    save(
        OUT / "attempt.json",
        {"registration_sha256": sha(PLAN), "started_at_utc": datetime.now(UTC).isoformat()},
    )
    for case in plan["cases"]:
        inputs = {
            s: {**item, "features": signals(item, plan["signal"], case)}
            for s, item in panel.items()
        }
        pulses = pd.DataFrame({s: item["features"].momentum for s, item in inputs.items()})
        pulses.to_parquet(OUT / f"{case}-signals.parquet")
        for slip in plan["extra_cost_bps_per_side"]:
            key = f"{case}_{5 + slip}bps"
            account = replay(
                inputs, plan, "Momentum63", plan["capital_usdt"], slip, plan["start"], plan["end"]
            )
            path = OUT / key
            path.mkdir()
            account["curve"].to_parquet(path / "nav.parquet", index=False)
            for field in ("events", "decisions", "trades", "state"):
                save(path / f"{field}.json", account[field])
    build_result()


def build_result():
    plan = read(PLAN)
    if (HERE / "result.json").exists():
        raise FileExistsError("Study results already sealed")
    attempt_hash = read(OUT / "attempt.json")["registration_sha256"]
    if attempt_hash not in (sha(PLAN), plan.get("ledger_registration_sha256")):
        raise ValueError("Unregistered account ledgers")
    results = {}
    for case in plan["cases"]:
        for slip in plan["extra_cost_bps_per_side"]:
            key = f"{case}_{5 + slip}bps"
            path = OUT / key
            account = {
                field: read(path / f"{field}.json") for field in ("events", "trades", "state")
            }
            account["curve"] = pd.read_parquet(path / "nav.parquet")
            results[key] = summarize(account, plan)
            print(key, results[key]["net_return"], results[key]["closed_trades"], flush=True)
    failed = {}
    for cost in (8, 15):
        candidate = results[f"BasisFlush_{cost}bps"]
        control = results[f"PriceFlush_{cost}bps"]
        checks = {
            "minimum_completed_positions": candidate["closed_trades"] >= 30,
            "minimum_active_weeks": candidate["completed_position_entry_weeks"] >= 12,
            "full_net_excess": candidate["net_return"] > max(0, control["net_return"]),
            "drawdown": candidate["max_drawdown"] <= 0.35 and not candidate["account_halted"],
            **{
                f"net_excess_{period}": net > max(0, control["period_net_returns"][period])
                for period, net in candidate["period_net_returns"].items()
            },
        }
        failed[str(cost)] = [label for label, passes in checks.items() if not passes]
    save(
        HERE / "result.json",
        {
            "registration_sha256": sha(PLAN),
            "accounts": results,
            "failed_gates": failed,
            "historical_screen_passed": not any(failed.values()),
            "upside_screen_passed": results["BasisFlush_15bps"]["annualized_net_return"] >= 0.20,
            "paper_eligible": False,
            "production_eligible": False,
        },
    )


if __name__ == "__main__":
    {"register": register, "run": run, "result": build_result}[sys.argv[1]]()
