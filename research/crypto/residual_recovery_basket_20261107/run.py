"""Test the frozen A15 recovery signal as a standalone funded perpetual book."""

import math
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.calendar_portfolio import replay
from research.crypto.listing_reversal_20261107 import catalog
from research.crypto.nfi_residual_recovery_20261107 import study as a15
from research.crypto.spot_perp_flow_gap_20261107 import audit as account_audit
from research.crypto.spot_perp_flow_gap_20261107 import inputs as flow


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = ROOT / "user_data/crypto_quant"
OUT = DATA / "residual_recovery_basket_20261107_v001"
PLAN = HERE / "registration.json"
RESULT = HERE / "result.json"
MARKET = DATA / "funding_breakout_20261107_v001/data"
UNIVERSE = ROOT / "research/crypto/funding_breakout_20261107/registration.json"
DAILY = a15.a13.PLAN
CASES = ("RelativeRecovery", "AbsoluteRecovery", "ResidualDrop")
MODEL = {
    "capital": 1000,
    "fee_bps": 5,
    "gross": 0.95,
    "max_positions": 2,
    "participation": 0.01,
    "stop_loss": 1.0,
    "drawdown_halt": 0.35,
}
WINDOWS = (
    {"name": "2024", "start": "2024-01-01", "end": "2025-01-01"},
    {"name": "2025-26", "start": "2025-01-01", "end": "2026-09-01"},
)


def register():
    if PLAN.exists() or OUT.exists():
        raise FileExistsError("Study already registered")
    raw = read(DAILY)
    symbols = read(UNIVERSE)["symbols"]
    market = read(MARKET / "receipts.json")
    daily_path = Path(raw["data_receipts"]["path"])
    if sha(daily_path) != raw["data_receipts"]["sha256"]:
        raise ValueError("Sealed daily manifest changed")
    daily = read(daily_path)
    sources = (
        Path(__file__),
        daily_path,
        HERE / "audit.py",
        UNIVERSE,
        DAILY,
        a15.RESULT,
        a15.PROTOCOL,
        Path(a15.__file__),
        Path(flow.__file__),
        Path(catalog.__file__),
        Path(account_audit.__file__),
        ROOT / "research/crypto/calendar_portfolio.py",
        ROOT / "freqtrade/research/data.py",
        MARKET / "receipts.json",
    )
    checksums = {str(p.relative_to(ROOT)): sha(p) for p in sources}
    for s in symbols:
        native_name = s.removesuffix("USDT") + "_USDT_USDT-1d-futures.feather"
        for path, digest in (
            (MARKET / f"{s}_5m.parquet", market[f"{s}_5m.parquet"]),
            (MARKET / f"{s}_funding.parquet", market[f"{s}_funding.parquet"]),
            (Path(raw["datadir"]) / "futures" / native_name, daily[f"futures/{native_name}"]),
        ):
            if sha(path) != digest:
                raise ValueError(f"Original market archive changed: {path}")
            checksums[str(path.relative_to(ROOT))] = digest
    save(
        PLAN,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "checksums": checksums,
            "symbols": symbols,
            "windows": WINDOWS,
            "model": MODEL,
            "cases": CASES,
            "cost_bps_per_side": [8, 15],
            "hypothesis": (
                "The earlier A15 signal may identify tradable recovery beyond the few "
                "X8 campaigns with the same input feature."
            ),
            "feature": (
                "Keep A15 unchanged: 68 completed UTC closes, fit OLS slope on oldest "
                "60 daily log returns then calculate asset minus beta times BTC returns "
                "over final seven days. RelativeRecovery requires 7-day sum <= log(0.97) "
                "and last daily residual > 0. AbsoluteRecovery applies those thresholds to "
                "raw coin returns. ResidualDrop uses the residual threshold without the "
                "last-day confirmation. BTC is the risk reference only in all three books. "
                "Use only consecutive exact closes, without fills or inferred observations."
            ),
            "portfolio": (
                "Long only, daily 01:00 UTC decisions after the prior day closes. Open "
                "only on a false-to-true edge (including first eligible in-window day "
                "if its prior day was false), at most two scheduled names, rank new edges "
                "by most negative seven-day score then symbol. Retain admitted sleeve up "
                "to five UTC decision days, or exit at the next daily decision where "
                "its seven-day score reaches zero. Ignore new edges for active sleeves "
                "and unadmitted edges expire that day. Target each sleeve 47.5% of NAV. "
                "Rebalance only if target inventory changes. Attempt terminal exits from "
                "23:00 UTC on the last window day. Both windows start from cash. "
                "A stop at 35% portfolio drawdown halts new entries; no extra asset stop."
            ),
            "execution": (
                "Funded 1x equivalent book, no concurrent sleeve financing beyond shared wallet. "
                "Existing capacity and delayed-reversal replay with next five-minute trade close "
                "plus adverse 3/10 bps and 5 bps quote fee. Use exact official funding timestamps, "
                "mark and rate, checked against sealed normalized funding rates. Drawdown at "
                "five-minute transaction closes including modeled exit costs. Existing account "
                "stop may overshoot, and order-book fills require separate proof."
            ),
            "decision": (
                "At both costs in both reset windows, relative recovery requires positive "
                "net return beating both controls, at least 60 completed campaigns across "
                "30 distinct entry days, drawdown <=35%, no halt and terminal flat. "
                "At 15bps require at least 25% annualized net in each window. Fixed survivor "
                "universe and retrospective windows. Retain all results. No orders."
            ),
            "orders_enabled": False,
        },
    )


def checked_plan():
    plan = read(PLAN)
    for name, digest in plan["checksums"].items():
        if sha(ROOT / name) != digest:
            raise ValueError(f"Frozen input changed: {name}")
    return plan


def capture(plan):
    flow.OUT = OUT
    (OUT / "raw").mkdir(parents=True)

    def fetch(symbol):
        pages = {}
        published = flow.funding(plan, symbol, pages)
        expected = pd.read_parquet(MARKET / f"{symbol}_funding.parquet").funding_rate
        expected = expected.loc["2024-01-01":"2026-08-31"]
        active = published.loc[published.index < pd.Timestamp("2026-09-01", tz="UTC")]
        exact = active.funding_rate.set_axis(active.index.floor("h"))
        if not exact.index.as_unit("us").equals(expected.index.as_unit("us")) or not np.allclose(
            exact.to_numpy(), expected.to_numpy(), rtol=0, atol=1e-13
        ):
            raise ValueError(f"Published funding coverage or rates differ: {symbol}")
        path = OUT / f"{symbol}-funding.parquet"
        published.to_parquet(path)
        return symbol, {"frame_sha256": sha(path), "rows": len(published), "pages": pages}

    with ThreadPoolExecutor(max_workers=6) as pool:
        receipt = dict(pool.map(fetch, plan["symbols"]))
    save(OUT / "funding_admission.json", receipt)


def load(plan):
    m = read(OUT / "funding_admission.json")
    root = Path(read(DAILY)["datadir"]) / "futures"
    data, closes = {}, {}
    for s in plan["symbols"]:
        item = m[s]
        path = OUT / f"{s}-funding.parquet"
        if sha(path) != item["frame_sha256"] or any(
            sha(OUT / "raw" / n) != v["sha256"] for n, v in item["pages"].items()
        ):
            raise ValueError(f"Funding admission changed: {s}")
        bars = pd.read_parquet(MARKET / f"{s}_5m.parquet")
        day = pd.read_feather(root / f"{s.removesuffix('USDT')}_USDT_USDT-1d-futures.feather")
        series = day.set_index("date").close.asfreq("D")
        if series.isna().any() or (series <= 0).any():
            raise ValueError(f"Missing or invalid daily candles: {s}")
        data[s] = {"bars": bars, "funding": pd.read_parquet(path)}
        closes[s] = series
    frame = pd.DataFrame(closes)
    if frame.isna().any().any():
        raise ValueError("Daily asset panels do not align")
    return data, frame


def feature_panel(close, case):
    returns = np.log(close).diff().iloc[1:]
    windows = np.lib.stride_tricks.sliding_window_view(returns.to_numpy(), 67, axis=0)
    if case == "AbsoluteRecovery":
        exact = close.map(lambda price: Fraction(str(price)))
        score = (exact / exact.shift(7) - 1).iloc[67:]
        ready = score.le(Fraction(-3, 100)) & close.gt(close.shift(1)).loc[score.index]
    else:
        benchmark = np.lib.stride_tricks.sliding_window_view(returns.BTCUSDT.to_numpy(), 67)
        b = benchmark[:, :60] - benchmark[:, :60].mean(axis=1, keepdims=True)
        history = windows[:, :, :60]
        a = history - history.mean(axis=2, keepdims=True)
        denom = (b**2).sum(axis=1)
        beta = (a * b[:, None, :]).sum(axis=2) / np.where(denom > 0, denom, np.nan)[:, None]
        residual = windows[:, :, -7:] - beta[:, :, None] * benchmark[:, None, -7:]
        score = pd.DataFrame(residual.sum(axis=2), index=returns.index[66:], columns=close.columns)
        ready = score.le(math.log(0.97))
        if case == "RelativeRecovery":
            ready &= residual[:, :, -1] > 0
    ready["BTCUSDT"] = False
    return score, ready


def targets(close, case, window):
    score, ready = feature_panel(close, case)
    new = ready & ~ready.shift(1, fill_value=False)
    start, end = (pd.Timestamp(window[key], tz="UTC") for key in ("start", "end"))
    grid = pd.date_range(start, end, freq="h", inclusive="left")
    active = {}
    decisions = {}
    old = ()
    for t in grid[grid.hour == 1]:
        day = t.normalize() - pd.Timedelta(days=1)
        active = {
            s: expires for s, expires in active.items() if t < expires and score.at[day, s] < 0
        }
        ranked = sorted(
            (s for s in score if new.at[day, s] and s not in active),
            key=lambda s: (score.at[day, s], s),
        )
        for s in ranked[: 2 - len(active)]:
            active[s] = t + pd.Timedelta(days=5)
        current = tuple(sorted(active))
        if current != old:
            decisions[t] = {s: 0.5 for s in active}
        old = current
    decisions[grid[-1]] = {}
    weights = pd.DataFrame.from_dict(decisions, orient="index").reindex(
        index=grid, columns=close.columns
    )
    return (
        weights.fillna(0)
        .add_prefix("target_")
        .assign(rebalance=grid.isin(decisions), risk_scale=1.0)
    )


def account(data, plan, window, case, cost, schedule):
    name = f"{window['name']}_{case}_{cost}bps"
    path = OUT / name
    path.mkdir()
    schedule.to_parquet(path / "targets.parquet")
    result = replay(
        {s: item["bars"] for s, item in data.items()},
        {s: item["funding"] for s, item in data.items()},
        schedule,
        {**plan["model"], "slippage_bps": cost - plan["model"]["fee_bps"]},
        window["start"],
        window["end"],
    )
    curve = result["curve"]
    curve.to_parquet(path / "nav.parquet", index=False)
    for key in ("events", "trades", "requests", "resume_state"):
        save(path / f"{key}.json", result[key])
    nav = curve.equity
    years = (curve.date.iloc[-1] - curve.date.iloc[0]).days / 365.25
    summary = {
        "case": case,
        "cost_bps": cost,
        "window": window["name"],
        "net_return": float(nav.iloc[-1] / nav.iloc[0] - 1),
        "annualized_net_return": float((nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1),
        "max_drawdown": float((1 - nav / nav.cummax()).max()),
        "completed": len(result["trades"]),
        "open_positions": len(result["positions"]),
        "entry_days": len({pd.Timestamp(t["entry_time"]).date() for t in result["trades"]}),
        "risk_halted": result["risk_halted"],
        "capacity": result["capacity"],
        "hashes": {p.name: sha(p) for p in path.iterdir()},
    }
    print(name, summary["net_return"], summary["max_drawdown"], summary["completed"], flush=True)
    return name, summary


def reuse_or_run(data, plan, window, case, cost, schedule, old):
    name = f"{window['name']}_{case}_{cost}bps"
    if not old:
        return account(data, plan, window, case, cost, schedule)
    row = old["accounts"][name]
    path = OUT / name
    for file, digest in row["hashes"].items():
        if sha(path / file) != digest:
            raise ValueError("Original ledger changed before technical recovery")
    targets = pd.read_parquet(path / "targets.parquet")
    if schedule.equals(targets):
        return name, row
    if case != "AbsoluteRecovery":
        raise ValueError("Technical recovery changed the residual signal")
    path.rename(OUT / "attempt-v001" / name)
    return account(data, plan, window, case, cost, schedule)


def run(plan):
    data, close = load(plan)
    accounts, checks = {}, {}
    old = None
    if revision := plan.get("technical_amendment"):
        original = OUT / "attempt-v001/result.json"
        if sha(original) != revision["prior_result_sha256"]:
            raise ValueError("Original result changed before technical recovery")
        old = read(original)
        if old["funding_admission_sha256"] != sha(OUT / "funding_admission.json"):
            raise ValueError("Funding intake changed before technical recovery")
    for window in plan["windows"]:
        for case in plan["cases"]:
            schedule = targets(close, case, window)
            for cost in plan["cost_bps_per_side"]:
                name, row = reuse_or_run(data, plan, window, case, cost, schedule, old)
                accounts[name] = row
        for cost in plan["cost_bps_per_side"]:
            a, b, c = (accounts[f"{window['name']}_{case}_{cost}bps"] for case in plan["cases"])
            checks[f"{window['name']}_{cost}bps"] = {
                "returns": a["net_return"] > max(0, b["net_return"], c["net_return"]),
                "risk": a["max_drawdown"] <= 0.35 and not a["risk_halted"],
                "sample": a["completed"] >= 60
                and a["entry_days"] >= 30
                and not a["open_positions"],
                "upside": cost != 15 or a["annualized_net_return"] >= 0.25,
            }
        save(
            RESULT,
            {
                "registration_sha256": sha(PLAN),
                "funding_admission_sha256": sha(OUT / "funding_admission.json"),
                "accounts": accounts,
                "gates": checks,
                "passed": len(checks) == 4 and all(all(g.values()) for g in checks.values()),
                "paper_eligible": False,
            },
        )


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "register":
        register()
    else:
        {"capture": capture, "run": run}[command](checked_plan())
