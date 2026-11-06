"""Test a frozen two-market flow factor in the existing calendar account."""

import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.calendar_portfolio import replay
from research.crypto.listing_reversal_20261107 import catalog
from research.crypto.spot_perp_flow_gap_20261107 import inputs


HERE = Path(__file__).resolve().parent
PLAN = HERE / "registration.json"
RESULT = HERE / "result.json"
OUT = inputs.OUT
ROOT = inputs.ROOT
ENGINE = ROOT / "research/crypto/calendar_portfolio.py"
SYMBOLS = (
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
)
CASES = ("Gap", "SpotOnly", "PerpOnly")


def register():
    if PLAN.exists() or OUT.exists():
        raise FileExistsError("Flow gap already registered")
    bar_manifest = inputs.BARS / "receipts.json"
    manifest = read(bar_manifest)
    bars = [inputs.BARS / f"{s}_5m.parquet" for s in SYMBOLS]
    for path in bars:
        if sha(path) != manifest[path.name]:
            raise ValueError(f"Sealed parent data changed: {path}")
    sources = [
        Path(__file__),
        HERE / "audit.py",
        Path(inputs.__file__),
        Path(catalog.__file__),
        ROOT / "freqtrade/research/data.py",
        ENGINE,
        bar_manifest,
        *bars,
    ]
    save(
        PLAN,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "checksums": {str(path.relative_to(ROOT)): sha(path) for path in sources},
            "symbols": SYMBOLS,
            "cases": CASES,
            "data_start": "2023-11-01",
            "windows": [
                {"name": "2024", "start": "2024-01-01", "end": "2025-01-01"},
                {"name": "2025-26", "start": "2025-01-01", "end": "2026-09-01"},
            ],
            "hypothesis": (
                "Relative spot buying pressure versus same-asset perpetual buying pressure "
                "identifies return continuation that each single-market imbalance misses."
            ),
            "signal": (
                "Per market, quote-volume-weighted taker buy fraction over three completed UTC "
                "daily candles minus the analogous fraction over the preceding nonoverlapping "
                "30 days. Gap equals spot anomaly minus perpetual anomaly. Controls use "
                "only one of the two anomalies, identical account policy. At 08:00 UTC Monday "
                "and Thursday, rank the ten assets using data through the previous UTC day, "
                "long the top three and short the bottom three, with alphabetical tie break. "
                "Require all ten pairs have both market scores. Empty target begins 23:00 UTC "
                "on the last day of each window to attempt a normal volume-bounded liquidation."
            ),
            "model": {
                "capital": 1000,
                "fee_bps": 5,
                "gross": 0.95,
                "max_positions": 6,
                "participation": 0.01,
                "stop_loss": 0.12,
                "drawdown_halt": 0.35,
            },
            "execution": (
                "Existing calendar replay, 1x gross margin equivalent, six equal targets "
                "with 47.5% per side at rebalance, one-percent preceding-hour/interval volume "
                "caps, 5m close plus adverse slippage, exact published funding times and mark "
                "values. Actual exposures drift and fills may be asymmetric. 12% price stop "
                "and 35% account halt use the engine's delayed execution. Observe drawdown on "
                "5m transaction closes including exit cost. Record open terminal inventory."
            ),
            "total_cost_bps_per_side": [8, 15],
            "gate": (
                "Each window independently starts from 1000 USDT in cash. At both costs and "
                "in both windows require positive net return above both single-market controls, "
                "no account drawdown halt, maximum 5m close-based account drawdown at most 35%, "
                "at least 60 completed position lifecycles and no remaining positions. At 15 bps "
                "also require at least 25% annualized net return in each window for an upside "
                "admission. Positive results still require held-out and marked-risk evaluation."
            ),
            "evidence_scope": (
                "Fixed incumbent liquid cohort with survivorship bias. Both windows and fee "
                "schedules frozen before candidate economics. All historical, no orders."
            ),
            "candidate_economics_observed": False,
            "paper_eligible": False,
        },
    )


def checked_plan():
    plan = read(PLAN)
    for name, checksum in plan["checksums"].items():
        if sha(ROOT / name) != checksum:
            raise ValueError(f"Frozen research input differs: {name}")
    return plan


def anomaly(frame):
    recent = frame[["buy_quote", "quote"]].rolling(3, min_periods=3).sum()
    history = frame[["buy_quote", "quote"]].shift(3).rolling(30, min_periods=30).sum()
    return recent.buy_quote / recent.quote - history.buy_quote / history.quote


def signal(data, case, window):
    symbols = list(data)
    begin, end = (pd.Timestamp(window[k], tz="UTC") for k in ("start", "end"))
    grid = pd.date_range(begin, end, freq="h", inclusive="left")
    closes = {}
    for symbol, item in data.items():
        spot, perp = (anomaly(item[k]) for k in ("spot", "perp"))
        closes[symbol] = {"Gap": spot - perp, "SpotOnly": spot, "PerpOnly": perp}[case]
    factor = pd.DataFrame(closes)
    factor.index += pd.Timedelta(days=1)
    weights = np.zeros((len(grid), len(symbols)))
    scheduled = ((grid.hour == 8) & grid.dayofweek.isin([0, 3])) | (
        grid == end - pd.Timedelta(hours=1)
    )
    for i in np.flatnonzero(scheduled):
        if grid[i] == end - pd.Timedelta(hours=1):
            continue
        scores = factor.loc[grid[i].normalize(), symbols]
        if not np.isfinite(scores).all():
            continue
        order = sorted(range(len(symbols)), key=lambda j: (-scores.iloc[j], symbols[j]))
        weights[i, order[:3]] = 1 / 6
        weights[i, order[-3:]] = -1 / 6
    target = pd.DataFrame(weights, index=grid, columns=["target_" + s for s in symbols])
    return target.assign(rebalance=scheduled, risk_scale=1.0)


def account(plan, data, case, cost, window):
    name = f"{window['name']}_{case}_{cost}bps"
    path = OUT / name
    path.mkdir()
    targets = signal(data, case, window)
    targets.to_parquet(path / "targets.parquet")
    result = replay(
        {s: item["bars"] for s, item in data.items()},
        {s: item["funding"] for s, item in data.items()},
        targets,
        {**plan["model"], "slippage_bps": cost - plan["model"]["fee_bps"]},
        window["start"],
        window["end"],
    )
    curve = result["curve"]
    net = curve.equity / plan["model"]["capital"]
    years = (curve.date.iloc[-1] - curve.date.iloc[0]).days / 365.25
    curve.to_parquet(path / "nav.parquet", index=False)
    for key in ("events", "trades", "requests", "resume_state"):
        save(path / f"{key}.json", result[key])
    summary = {
        "window": window["name"],
        "case": case,
        "cost_bps": cost,
        "net_return": float(net.iloc[-1] - 1),
        "annualized_net_return": float(net.iloc[-1] ** (1 / years) - 1),
        "max_5m_close_drawdown": float((1 - net / net.cummax()).max()),
        "max_absolute_net_exposure_fraction": float(
            (curve.net_exposure.abs() / curve.equity).max()
        ),
        "completed_positions": len(result["trades"]),
        "open_positions": len(result["positions"]),
        "risk_halted": result["risk_halted"],
        "capacity": result["capacity"],
        "hashes": {p.name: sha(p) for p in path.iterdir()},
    }
    print(
        name,
        summary["net_return"],
        summary["max_5m_close_drawdown"],
        summary["completed_positions"],
        flush=True,
    )
    save(path / "summary.json", summary)
    return name, summary


def run():
    plan = checked_plan()
    if RESULT.exists():
        raise FileExistsError("Economic result already exists")
    data = inputs.panel(plan)
    accounts, gates = {}, {}
    for window in plan["windows"]:
        for cost in plan["total_cost_bps_per_side"]:
            rows = {}
            for case in plan["cases"]:
                name, row = account(plan, data, case, cost, window)
                rows[case] = accounts[name] = row
            gap = rows["Gap"]
            gates[f"{window['name']}_{cost}bps"] = {
                "beats_controls_and_cash": gap["net_return"]
                > max(0, rows["SpotOnly"]["net_return"], rows["PerpOnly"]["net_return"]),
                "risk": not gap["risk_halted"] and gap["max_5m_close_drawdown"] <= 0.35,
                "sample_and_flat": gap["completed_positions"] >= 60 and gap["open_positions"] == 0,
                "upside": cost < 15 or gap["annualized_net_return"] >= 0.25,
            }
    save(
        RESULT,
        {
            "registration_sha256": sha(PLAN),
            "admission_sha256": sha(OUT / "admission.json"),
            "accounts": accounts,
            "gates": gates,
            "passed": all(all(row.values()) for row in gates.values()),
            "paper_eligible": False,
        },
    )


if __name__ == "__main__":
    {"register": register, "capture": lambda: inputs.capture(checked_plan()), "run": run}[
        sys.argv[1]
    ]()
