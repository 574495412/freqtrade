"""Reconcile the paired 2024 drawdowns using the already audited native orders."""

from collections import defaultdict
from math import isclose
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.head_shoulders_financial_native_20261117_v3.independent_account_audit_v2 import trade_path
from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .continuous_native_inputs import candle_filename
from .volatility_across_years import annual_plan, selected_receipt


HERE = Path(__file__).resolve().parent


def worst_drawdown(equity):
    loss = 1 - equity.div(equity.cummax())
    trough = loss.idxmax()
    prior = equity.loc[:trough]
    peak = prior[prior.eq(prior.max())].index[-1]
    return peak, trough, float(loss.at[trough])


def market_streams(plan, fingerprints, pair):
    tapes = {}
    for kind in ("5m-mark", "1h-mark", "1h-funding_rate"):
        name = candle_filename(pair, kind)
        source = {"path": str(Path(plan["datadir"]) / name), "sha256": fingerprints[name]}
        frame = pd.read_feather(checked(source)).set_index("date")
        frame.index = frame.index.as_unit("ns")
        assert frame.index.is_unique and frame.index.is_monotonic_increasing
        tapes[kind] = frame
    rate = tapes["1h-funding_rate"]["funding_rate"]
    hourly = tapes["1h-mark"]["open"]
    settlement = (rate * hourly.reindex(rate.index)).to_numpy()
    return tapes["5m-mark"]["open"], rate.index.as_unit("ms").asi8, settlement


def interval_attribution(plan, nav, trades, start, end, fingerprints, prices):
    dates = pd.DatetimeIndex([start, end]).as_unit("ns")
    clocks = dates.as_unit("ms").asi8
    changes = defaultdict(float)
    gross = np.zeros(2)
    sides = {"long": np.zeros(2, dtype=int), "short": np.zeros(2, dtype=int)}
    campaigns = 0
    for trade in trades:
        opened, closed = (trade[f"{name}_timestamp"] for name in ("open", "close"))
        if closed < clocks[0] or opened >= clocks[-1]:
            continue
        pair = trade["pair"]
        if pair not in prices:
            prices[pair] = market_streams(plan, fingerprints, pair)
        marks, events, units = prices[pair]
        held = (clocks > opened) & (clocks <= closed)
        raw = marks.reindex(dates).to_numpy()
        assert np.isfinite(raw[held]).all() and np.all(raw[held] > 0)
        funding_events = (events >= opened) & (events <= closed)
        assert np.isfinite(units[funding_events]).all()
        path, exposure, _, funding, _ = trade_path(
            trade, clocks, np.where(held, raw, 0.0), events[funding_events], units[funding_events],
        )
        assert isclose(funding, trade["funding_fees"], abs_tol=1e-7)
        changes[pair] += float(path[1] - path[0])
        gross += exposure
        side = "short" if trade["is_short"] else "long"
        sides[side] += held
        campaigns += 1
    marks = nav.loc[dates]
    delta = marks.marked_equity.iloc[-1] - marks.marked_equity.iloc[0]
    residual = float(sum(changes.values()) - delta)
    assert abs(residual) < 3e-5
    np.testing.assert_array_equal(sides["long"] + sides["short"], marks.open_trades)
    by_pair = sorted(changes.items(), key=lambda item: (item[1], item[0]))
    snapshots = []
    for i, (clock, mark) in enumerate(marks.iterrows()):
        snapshots.append({
            "clock": clock.isoformat(), "nav_usdt": float(mark.marked_equity),
            "long_positions": int(sides["long"][i]), "short_positions": int(sides["short"][i]),
            "mark_gross_notional_to_equity_reconstructed": float(gross[i] / mark.marked_equity),
        })
    return {"snapshots": snapshots, "interval_return": float(marks.marked_equity.iloc[-1] / marks.marked_equity.iloc[0] - 1),
            "summed_change_usdt": float(delta), "reconciliation_error_usdt": residual,
            "overlapping_native_campaigns": campaigns, "pnl_change_by_pair_usdt": dict(by_pair)}


def main():
    evidence = receipt(HERE / "annual_2024_boost14_15bp_verified.json")
    summary = read(checked(evidence))
    wallet = {}
    for role, values in summary["accounts"].items():
        plan, _, sources = annual_plan(values, 2024)
        native = selected_receipt(values["receipts"], ".zip")
        frame = pd.read_parquet(checked(sources["nav"])).set_index("date")
        peak, trough, loss = worst_drawdown(frame.marked_equity)
        assert isclose(loss, values["drawdown"], abs_tol=1e-12)
        wallet[role] = {
            "sources": sources | {"native_trades": native}, "nav": frame, "plan": plan,
            "trades": stats_from(checked(native))["trades"], "worst": (peak, trough, loss),
            "fingerprints": read(checked(plan["data_receipts"])), "prices": {},
        }
    comparisons = {}
    for role, owner in wallet.items():
        start, end, drawdown = owner["worst"]
        rows = {}
        for comparison, data in wallet.items():
            rows[comparison] = interval_attribution(data["plan"], data["nav"], data["trades"], start, end,
                                                   data["fingerprints"], data["prices"])
        assert isclose(-rows[role]["interval_return"], drawdown, abs_tol=1e-12)
        comparisons[role] = {"start": start.isoformat(), "end": end.isoformat(),
                             "portfolio_drawdown": drawdown, "same_interval": rows}
    write_once(HERE / "annual_2024_capacity_drawdown_attribution.json", {
        "account_comparison": evidence, "account_sources": {r: w["sources"] for r, w in wallet.items()},
        "windows": comparisons, "source": receipt(Path(__file__)), "cost_per_side_bp": 15,
        "new_native_accounts": 0, "orders_enabled": False,
    })


if __name__ == "__main__":
    main()
