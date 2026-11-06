"""Measure independent mark risk and the cost of keeping the 8bp fill path fixed."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation
from research.crypto.oi_breakout_20261107 import run as study


HERE = Path(__file__).resolve().parent
OUT = study.OUT / "mark_and_cost"
STEP = pd.Timedelta(minutes=5)


def value(events, grid, execution, quotes, fee, slip, capital):
    """Synthetic full-notional bookkeeping reproduces the margin-account NAV."""
    cash = np.zeros(len(grid))
    cash[0] = capital
    inventory = {s: np.zeros(len(grid)) for s in execution}
    for event in events:
        time = pd.Timestamp(event.get("effective_time", event["time"]))
        i = grid.get_loc(time)
        if event["type"] == "funding":
            cash[i] += event["cash_delta"]
            continue
        s = event["symbol"]
        delta = event["side"] * event["quantity"] * (1 if event["type"] == "entry" else -1)
        price = execution[s][i] * (1 + np.sign(delta) * slip)
        cash[i] -= delta * price + abs(delta) * price * fee
        inventory[s][i] += delta
    nav = cash.cumsum()
    for s, changes in inventory.items():
        qty = changes.cumsum()
        nav += (qty - np.abs(qty) * (fee + slip)) * quotes[s]
    return nav


def risk(nav, grid):
    dd = 1 - nav / np.maximum.accumulate(nav)
    hit = np.flatnonzero(dd > 0.35)
    i = int(dd.argmax())
    j = int(nav[: i + 1].argmax())
    return {
        "net_return": float(nav[-1] / nav[0] - 1),
        "max_drawdown": float(dd[i]),
        "peak": grid[j].isoformat(),
        "trough": grid[i].isoformat(),
        "first_over_35pct": grid[hit[0]].isoformat() if len(hit) else None,
    }


def main():
    plan = study.checked_plan()
    result = read(study.RESULT)
    verification = read(HERE / "verification.json")
    if verification["result_sha256"] != sha(study.RESULT):
        raise ValueError("Economics lack independent audit")
    OUT.mkdir()
    policy = OUT / "registration.json"
    save(
        policy,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "original_result_sha256": sha(study.RESULT),
            "verification_sha256": sha(HERE / "verification.json"),
            "source_sha256": sha(Path(__file__)),
            "mark_source_sha256": sha(Path(mark_valuation.__file__)),
            "policy": (
                "Post-result diagnostics only. Reconcile exact 5m transaction-close NAV "
                "before measuring official 5m mark open NAV. Keep trade and funding ledger. "
                "Separately charge 15bps to 8bps expansion quantities/times with identical "
                "funding, no resizes or hypothetical risk actions. Preserve primary results."
            ),
        },
    )
    start, _, end = study.bounds(plan["windows"][0])
    grid = pd.date_range(start, end, freq="5min").as_unit("us")
    mark_valuation.OUT = OUT

    def capture(symbol):
        pair = f"{symbol.removesuffix('USDT')}/USDT:USDT"
        _, mark, receipt = mark_valuation.month_mark(pair, grid)
        return symbol, mark.to_numpy(), receipt

    with ThreadPoolExecutor(max_workers=6) as pool:
        prices = list(pool.map(capture, plan["symbols"]))
    execution = {
        s: pd.read_parquet(study.MARKET / f"{s}_5m.parquet").close.loc[grid - STEP].to_numpy()
        for s in plan["symbols"]
    }
    marks = {s: p for s, p, _ in prices}
    fee = plan["model"]["fee_bps"] / 10000
    capital = plan["model"]["capital"]
    diagnostics = {}
    for name, row in result["accounts"].items():
        path = study.OUT / name
        for file in ("events.json", "nav.parquet"):
            if sha(path / file) != row["hashes"][file]:
                raise ValueError("Original evidence hash differs")
        events = read(path / "events.json")
        curve = pd.read_parquet(path / "nav.parquet")
        if not pd.DatetimeIndex(curve.date).equals(grid):
            raise ValueError("Original NAV clock differs")
        slip = row["cost_bps"] / 10000 - fee
        native = value(events, grid, execution, execution, fee, slip, capital)
        error = float(np.max(np.abs(native - curve.equity)))
        if error > 1e-7:
            raise ValueError(f"Transaction-close NAV parity failed {name}: {error}")
        mark = value(events, grid, execution, marks, fee, slip, capital)
        book = {
            "transaction_close_risk": risk(native, grid),
            "mark_risk": risk(mark, grid),
            "max_original_nav_error_usdt": error,
        }
        if name == "2024_Expand_8bps":
            shock = value(events, grid, execution, execution, fee, 0.001, capital)
            book["fixed_8bps_fill_path_with_15bps_cost"] = risk(shock, grid)
            pd.DataFrame({"date": grid, "equity": shock}).to_parquet(
                OUT / "fixed_8bps_fill_path_with_15bps_cost.parquet", index=False
            )
        output = OUT / f"{name}.parquet"
        pd.DataFrame({"date": grid, "equity": mark}).to_parquet(output, index=False)
        diagnostics[name] = {**book, "mark_curve_sha256": sha(output)}
    threshold = diagnostics["2024_Expand_15bps"]["transaction_close_risk"]["first_over_35pct"]
    if threshold is not None:
        index = grid.get_loc(pd.Timestamp(threshold))
        for name in ("2024_Expand_8bps", "2024_Expand_15bps"):
            curve = pd.read_parquet(study.OUT / name / "nav.parquet").equity
            diagnostics[name]["equity_when_15bps_first_crossed_35pct"] = float(curve.iloc[index])
            diagnostics[name]["return_after_15bps_first_crossed_35pct"] = float(
                curve.iloc[-1] / curve.iloc[index] - 1
            )
    save(
        HERE / "mark_and_cost.json",
        {
            "registration_sha256": sha(policy),
            "original_result_sha256": sha(study.RESULT),
            "accounts": diagnostics,
            "mark_receipts": {s: r for s, _, r in prices},
            "new_candidate_accounts": 0,
            "paper_eligible": False,
        },
    )
    print(diagnostics, flush=True)


if __name__ == "__main__":
    main()
