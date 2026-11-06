"""Rebuild price/OI decision schedules and verify the frozen account ledger."""

from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.oi_breakout_20261107 import run as study
from research.crypto.spot_perp_flow_gap_20261107.audit import check_account


HERE = Path(__file__).resolve().parent


def raw_oi(symbol, feature, plan, sources):
    clocks = study.required_oi(feature, plan["signal"])
    needed = {str(t.date()) for t in clocks}
    observations = {}
    for day in sorted(needed):
        row = sources.get((symbol, day))
        if row is None:
            raise ValueError(f"Missing archive receipt {symbol}/{day}")
        path = Path(row["path"])
        if (
            sha(path) != row["sha256"]
            or sha(path.with_suffix(".zip.CHECKSUM")) != row["checksum_sha256"]
        ):
            raise ValueError("Raw OI archive digest mismatch")
        frame = study.parse_metrics(path.read_bytes(), symbol, day)
        observations.update(
            (r.raw_time, r.sum_open_interest) for r in frame.itertuples() if r.sum_open_interest > 0
        )
    series = pd.Series({t: observations.get(t, np.nan) for t in clocks})
    known = series.index[series.index.year == 2024]
    december = series.index[series.index.year == 2023]
    old = known.union(december)
    np.testing.assert_allclose(
        series.loc[old], study.historical_oi(symbol).loc[old], atol=0, rtol=0, equal_nan=True
    )
    return series, len(needed)


def price_path(bars, window, rule):
    """Independent rolling path uses explicit historical observations."""
    start, _, end = study.bounds(window)
    history = rule["lookback_hours"]
    offset = pd.Timedelta(minutes=rule["price_buffer_minutes"] + 5)
    clock = pd.date_range(start - pd.Timedelta(hours=history + 1), end, freq="h", inclusive="left")
    values = iter(bars.close.loc[clock - offset].to_numpy())
    trail = deque((next(values) for _ in range(history)), maxlen=history)
    previous_direction = 0
    rows = []
    for t, price in zip(clock[history:], values, strict=True):
        high, low = max(trail), min(trail)
        buffer = 1 + rule["breakout_buffer"]
        direction = 1 if price >= high * buffer else -1 if price <= low / buffer else 0
        edge = direction if direction != previous_direction else 0
        previous_direction = direction
        if t >= start:
            rows.append((price, high, low, edge))
        trail.append(price)
    return pd.DataFrame(
        rows, index=clock[history + 1 :], columns=["price", "upper", "lower", "edge"]
    )


def independent_targets(paths, raw, plan, case):
    rule = plan["signal"]
    lag = pd.Timedelta(minutes=rule["oi_buffer_minutes"])
    span = pd.Timedelta(hours=rule["oi_change_hours"])
    frames = {s: list(p.itertuples()) for s, p in paths.items()}
    calendar = next(iter(paths.values())).index
    positions = {}
    schedules = []
    for i, at in enumerate(calendar):
        for symbol, values in frames.items():
            row = values[i]
            if symbol in positions:
                side, start, cutoff = positions[symbol]
                if i - start >= rule["pulse_hours"] or side * (row.price - cutoff) < 0:
                    del positions[symbol]
            if not row.edge or symbol in positions:
                continue
            latest, prior = (raw[symbol].get(t) for t in (at - lag, at - lag - span))
            if latest is None or prior is None or not (np.isfinite(latest) and np.isfinite(prior)):
                continue
            difference = latest / prior - 1
            move = rule["minimum_oi_move"]
            if (case == "Expand" and difference < move) or (
                case == "Contract" and difference > -move
            ):
                continue
            boundary = row.upper if row.edge > 0 else row.lower
            positions[symbol] = (row.edge, i, boundary * (1 - row.edge * rule["invalidation"]))
        selected = sorted(positions, key=lambda s: (positions[s][1], s))[:2]
        schedules.append({s: positions[s][0] / 2 if s in selected else 0.0 for s in frames})
    schedules[-1] = dict.fromkeys(frames, 0.0)
    target = pd.DataFrame(schedules, index=calendar).add_prefix("target_")
    scheduled = target.ne(target.shift(fill_value=0)).any(axis=1)
    scheduled.iloc[-1] = True
    return target.assign(rebalance=scheduled, risk_scale=1.0)


def main():
    plan = study.checked_plan()
    result = read(study.RESULT)
    admission = read(study.OUT / "funding_admission.json")
    if result["registration_sha256"] != sha(study.PLAN) or result[
        "funding_admission_sha256"
    ] != sha(study.OUT / "funding_admission.json"):
        raise ValueError("Result funding or registration mismatch")
    for name, receipt in admission["files"].items():
        if sha(study.OUT / "raw" / name) != receipt["sha256"]:
            raise ValueError("Raw BTC funding hash mismatch")
    btc = study.OUT / "BTCUSDT-funding.parquet"
    if sha(btc) != admission["btc_frame_sha256"]:
        raise ValueError("BTC funding frame differs")
    data = {
        s: {
            "bars": pd.read_parquet(study.MARKET / f"{s}_5m.parquet"),
            "funding": pd.read_parquet(
                btc if s == "BTCUSDT" else study.FUNDING / f"{s}-funding.parquet"
            ),
        }
        for s in plan["symbols"]
    }
    sources = read(study.OI / "sources.json")
    if "2025-26" in result["gates"]:
        sources += read(study.OUT / "extension/archive_receipts.json")
    by_day = {(r["symbol"], r["day"]): r for r in sources if r["status"] == "VERIFIED"}
    evidence = {}
    for window in plan["windows"]:
        if window["name"] not in result["gates"]:
            continue
        paths = {s: price_path(item["bars"], window, plan["signal"]) for s, item in data.items()}
        verified = {s: raw_oi(s, path, plan, by_day) for s, path in paths.items()}
        raw = {s: v[0] for s, v in verified.items()}
        evidence[window["name"]] = {
            "raw_archives_verified": sum(v[1] for v in verified.values()),
            "accounts": {},
        }
        for case in plan["cases"]:
            expected = independent_targets(paths, raw, plan, case)
            for cost in plan["cost_bps_per_side"]:
                name = f"{window['name']}_{case}_{cost}bps"
                row = result["accounts"][name]
                folder = study.OUT / name
                for file, checksum in row["hashes"].items():
                    if sha(folder / file) != checksum:
                        raise ValueError("Frozen output hash differs")
                observed = pd.read_parquet(folder / "targets.parquet")
                pd.testing.assert_frame_equal(expected, observed, check_freq=False)
                evidence[window["name"]]["accounts"][name] = {
                    "decision_hours_checked": len(expected),
                    **check_account(plan, data, folder, row),
                }
    save(
        HERE / "verification.json",
        {
            "registration_sha256": sha(study.PLAN),
            "result_sha256": sha(study.RESULT),
            "independent_windows": evidence,
            "passed": True,
            "orders_enabled": False,
        },
    )
    print("Independent price, OI, funding, and equity audit passed", flush=True)


if __name__ == "__main__":
    main()
