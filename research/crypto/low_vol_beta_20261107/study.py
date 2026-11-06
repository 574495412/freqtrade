"""Size the existing low-volatility sleeves against their known-at BTC beta."""

import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.low_vol_portfolio_20261107 import account, attribution
from research.crypto.low_vol_portfolio_20261107 import study as prior
from research.crypto.nfi_strength_allocation_20261107.audit import read
from research.crypto.nfi_strength_allocation_20261107.register import DATA, record
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked, save, sha


HERE = Path(__file__).resolve().parent
OUT = DATA / "low_vol_beta_20261107_v001"
POLICY = HERE / "registration.json"
RESULT = HERE / "result.json"
BTC = "BTCUSDT"
CASES = {"DollarReserve": 1.6, "BetaReserve": 1.6, "BetaFull": 2.0}


def register():
    if POLICY.exists() or OUT.exists():
        raise FileExistsError("Beta allocation study already registered")
    parent = read(prior.RESULT)
    save(
        POLICY,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "sources": [
                record(path)
                for path in (
                    Path(__file__),
                    prior.POLICY,
                    prior.RESULT,
                    Path(account.__file__),
                    Path(prior.__file__),
                    Path(attribution.__file__),
                    checked(parent["funding_receipts"]),
                )
            ],
            "portfolio": (
                "Reuse 25-asset weekly 63-day low-volatility ranks and the exact earlier "
                "execution, funding admission, costs, model maintenance, 30% risk halt "
                "and order limits. No BTC position. Estimate each asset's beta to BTC "
                "using 63 completed daily simple returns with intercept, through the "
                "Sunday close. For each leg use the mean across its five selected coins. "
                "Set the fraction of absolute gross in longs to short-leg beta / sum "
                "of the two betas, clipped to [0.25,0.75]; equally weight within each "
                "leg. Invalid or nonpositive leg betas are data failures. Reuse earlier "
                "frozen 1x and 2x dollar-balanced accounts. New dollar and beta 1.6x "
                "targets leave nominal initial margin room, since model leverage is 2x; "
                "also run beta at the same aggressive 2x gross target."
            ),
            "windows": prior.WINDOWS,
            "cases": CASES,
            "gate": (
                "A beta case must meet the gate at both costs and in all three windows: "
                "zero held funding gaps, no halt, modeled maintenance breach or rejected "
                "orders; 5m DD <=25%; positive net with >=25% annualized and full net "
                "> both 1x and its same-gross dollar control by 1pp. Return/DD must "
                "exceed the same-gross control. Original known history remains separate "
                "from new candidate evidence. Passing would warrant independent follow-up."
            ),
            "mechanism_check": (
                "Audit formula, clocks and preserved ranks. Report target estimated "
                "signed beta, model exposure after lot/cash and at every active weekly "
                "checkpoint, plus realized daily BTC beta on common pre-halt intervals "
                "for same-gross accounts. Neither is proof of independent alpha."
            ),
            "orders_enabled": False,
        },
    )


def daily(frame):
    grouped = frame.close.resample("D")
    return grouped.last().where(grouped.count() == 288)


def beta_targets(raw, btc, original):
    panel = pd.DataFrame({s: daily(df) for s, df in raw.items()})
    changes = panel.pct_change(fill_method=None)
    benchmark = daily(btc).pct_change(fill_method=None)
    targets = {}
    details = {}
    for at, base in original.items():
        cutoff = at.floor("D") - pd.Timedelta(days=1)
        end = pd.date_range(end=cutoff, periods=63, freq="D")
        x = benchmark.reindex(end).to_numpy()
        r = changes.reindex(end).to_numpy()
        if not np.isfinite(x).all() or not np.isfinite(r).all() or np.var(x) <= 0:
            raise ValueError(f"Incomplete or flat 63-day beta history at {at}")
        xc = x - x.mean()
        betas = (xc @ r) / (xc @ xc)
        y = pd.Series(x, index=end)
        reference = np.array([y.cov(pd.Series(c, index=end)) / y.var() for c in r.T])
        if not np.allclose(betas, reference, atol=1e-10, rtol=0):
            raise ValueError("Independent slope arithmetic differs")
        long = float(betas[base > 0].mean())
        short = float(betas[base < 0].mean())
        if min(long, short) <= 0:
            raise ValueError("Selected sleeve beta is nonpositive")
        unbounded = short / (long + short)
        share = min(0.75, max(0.25, unbounded))
        target = np.where(base > 0, share / 5, np.where(base < 0, -(1 - share) / 5, 0))
        if not np.array_equal(np.sign(target), np.sign(base)) or not np.isclose(
            abs(target).sum(), 1
        ):
            raise ValueError("Unexpected rank membership or target gross")
        targets[at] = target
        details[at] = {
            "last_completed_day": cutoff.isoformat(),
            "betas": betas.tolist(),
            "dollar_beta_per_gross": float(base @ betas),
            "beta_sized_beta_per_gross": float(target @ betas),
            "long_fraction": share,
            "clipped": unbounded != share,
        }
    return targets, details


def curve(account_row):
    return pd.read_parquet(checked(account_row["curve"])).set_index("time").equity


def modeled_beta(row, grid, marks, detail):
    ledger = pd.read_parquet(checked(row["ledger"]))
    cumulative = np.zeros_like(marks)
    cumulative[ledger.row, ledger.column] = ledger.delta
    held = cumulative.cumsum(axis=0)
    nav = curve(row).to_numpy()
    values = []
    for at in sorted(at for at in detail if grid[0] <= at <= grid[-1]):
        i = grid.get_loc(at)
        if np.abs(held[i]).sum() <= 1e-8 or nav[i] <= 0:
            continue
        b = detail[at]["betas"]
        values.append(float((held[i] * marks[i]) @ np.array(b) / nav[i]))
    return {
        "weekly_active_checkpoints": len(values),
        "median_signed_beta": float(np.median(values)) if values else None,
        "median_abs_beta": float(np.median(np.abs(values))) if values else None,
    }


def daily_beta(one, two, grid, btc):
    # Use the same interval for the two accounts; stop before either enters cash.
    endpoints = [grid[-1]]
    for row in (one, two):
        if row["halted"]:
            end_i = int(pd.read_parquet(checked(row["ledger"])).row.max())
            endpoints.append(grid[end_i])
    at = pd.date_range(grid[0], min(endpoints), freq="D")
    x = btc.reindex(at).pct_change(fill_method=None).iloc[1:].to_numpy()
    if not np.isfinite(x).all():
        raise ValueError("Incomplete paired BTC regression")
    if len(x) < 10 or np.var(x) <= 0:
        return {"paired_daily_returns": len(x), "dollar_beta": None, "sized_beta": None}
    xc = x - x.mean()
    betas = []
    for row in (one, two):
        y = curve(row).reindex(at).pct_change(fill_method=None).iloc[1:].to_numpy()
        betas.append(float(xc @ y / (xc @ xc)))
    return {"paired_daily_returns": len(x), "dollar_beta": betas[0], "sized_beta": betas[1]}


def assess(candidate, dollar, unlevered):
    net = candidate["net"]
    dd = candidate["drawdown"]
    return {
        "funding": not candidate["missing_held_funding"],
        "risk": not candidate["halted"]
        and not candidate["modeled_margin_breach_clocks"]
        and dd <= 0.25,
        "execution": not (candidate["rejected_batches"] or candidate["over_capacity_closes"]),
        "absolute_return": net > 0
        and candidate["annualized"] is not None
        and candidate["annualized"] >= 0.25,
        "paired_excess": net > max(dollar["net"], unlevered["net"]) + 0.01,
        "risk_adjusted": net * dollar["drawdown"] > dollar["net"] * dd,
    }


def run():
    policy = read(POLICY)
    for ref in policy["sources"]:
        checked(ref)
    OUT.mkdir()
    old_protocol = read(prior.POLICY)
    old = read(prior.RESULT)
    plan = read(checked(old_protocol["parent"]))
    symbols = old_protocol["symbols"]
    raw = {s: prior.candles(plan, s) for s in symbols}
    btc = prior.candles(plan, BTC)
    ordinary = prior.weights(raw)
    targets, detail = beta_targets(raw, btc, ordinary)
    decisions = OUT / "beta_signals.json"
    save(decisions, {at.isoformat(): row for at, row in detail.items()})
    evidence = read(checked(old["funding_receipts"]))
    funded = {s: attribution.funding_from_receipt(evidence[s], s) for s in symbols}
    rules = account.snapshot(
        read(checked(plan["market_snapshot"])), [prior.symbol_pair(s) for s in symbols]
    )
    windows = {}
    for name, (a, b) in prior.WINDOWS.items():
        source = prior.leverage.a13.OUT / "5m" if name == "2024" else prior.long_mark.OUT
        admission = read(source / "admission.json")["marks"]
        grid = pd.date_range(prior.clock(a), prior.clock(b), freq="5min")
        data = account.make_inputs(grid, raw, funded, admission, prior.symbol_pair)
        accounts = {}
        for case, gross in CASES.items():
            weights = ordinary if case == "DollarReserve" else targets
            for cost in prior.COSTS:
                key = f"{case}_{cost}bps"
                row = account.simulate(
                    grid, data, weights, rules, gross, cost, OUT / f"{name}_{key}"
                )
                row["weekly_exposure"] = modeled_beta(row, grid, data["mark"], detail)
                accounts[key] = row
                print(name, key, row["net"], row["drawdown"], flush=True)
        comparisons = {}
        for cost in prior.COSTS:
            base = old["windows"][name][f"{name}_1x_{cost}bps"]
            full = old["windows"][name][f"{name}_2x_{cost}bps"]
            for case, dollar in (
                ("BetaReserve", accounts[f"DollarReserve_{cost}bps"]),
                ("BetaFull", full),
            ):
                key = f"{case}_{cost}bps"
                candidate = accounts[key]
                comparisons[key] = {
                    "same_gross_dollar_return": dollar["net"],
                    "same_gross_dollar_drawdown": dollar["drawdown"],
                    "checks": assess(candidate, dollar, base),
                    "realized_beta": daily_beta(dollar, candidate, grid, btc.open),
                }
        windows[name] = {"accounts": accounts, "comparison": comparisons}
    passes = {
        case: all(
            all(row["checks"].values())
            for window in windows.values()
            for key, row in window["comparison"].items()
            if key.startswith(case)
        )
        for case in ("BetaReserve", "BetaFull")
    }
    save(
        RESULT,
        {
            "registration_sha256": sha(POLICY),
            "signals": record(decisions),
            "windows": windows,
            "passing_cases": passes,
            "paper_eligible": False,
        },
    )


if __name__ == "__main__":
    {"register": register, "run": run}[sys.argv[1]]()
