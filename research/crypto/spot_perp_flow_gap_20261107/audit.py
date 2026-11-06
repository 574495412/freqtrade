"""Reconstruct target decisions, exact funding, fills and all five-minute equity."""

import math
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.spot_perp_flow_gap_20261107 import inputs
from research.crypto.spot_perp_flow_gap_20261107 import run as study


STEP = pd.Timedelta(minutes=5)


def check_targets(data, path, row):
    table = pd.read_parquet(path)
    dates = table.index
    expected_schedule = ((dates.hour == 8) & dates.dayofweek.isin([0, 3])) | (dates == dates[-1])
    if not np.array_equal(table.rebalance, expected_schedule):
        raise ValueError("Rebalance clock differs")
    targets = table.filter(like="target_")
    if targets.loc[~expected_schedule].ne(0).any().any() or targets.iloc[-1].ne(0).any():
        raise ValueError("Unscheduled or terminal position target")
    for clock, actual in targets.loc[expected_schedule].iloc[:-1].iterrows():
        day = clock.normalize()
        scores = {}
        for symbol, item in data.items():
            values = {}
            for venue in ("spot", "perp"):
                frame = item[venue].loc[day - pd.Timedelta(days=33) : day - pd.Timedelta(days=1)]
                if len(frame) != 33:
                    raise ValueError("Independent signal window incomplete")
                a, b = frame.iloc[-3:], frame.iloc[:30]
                values[venue] = math.fsum(a.buy_quote) / math.fsum(a.quote) - (
                    math.fsum(b.buy_quote) / math.fsum(b.quote)
                )
            scores[symbol] = {
                "Gap": values["spot"] - values["perp"],
                "SpotOnly": values["spot"],
                "PerpOnly": values["perp"],
            }[row["case"]]
        ranks = sorted(scores, key=lambda symbol: (-scores[symbol], symbol))
        expected = {f"target_{s}": 1 / 6 for s in ranks[:3]} | {
            f"target_{s}": -1 / 6 for s in ranks[-3:]
        }
        if any(v != expected.get(s, 0) for s, v in actual.items()):
            raise ValueError(f"Independent signal disagrees at {clock}")
    return int(expected_schedule.sum())


def check_account(plan, data, path, row):  # noqa: C901
    grid_frame = pd.read_parquet(path / "nav.parquet").set_index("date")
    window = next(w for w in plan["windows"] if w["name"] == row["window"])
    grid = pd.date_range(window["start"], window["end"], freq="5min", tz="UTC")
    if not grid_frame.index.equals(grid):
        raise ValueError("Missing account observations")
    symbols = plan["symbols"]
    index = {symbol: i for i, symbol in enumerate(symbols)}
    fee = plan["model"]["fee_bps"] / 10_000
    slip = (row["cost_bps"] - plan["model"]["fee_bps"]) / 10_000
    quantity = np.zeros((len(grid), len(symbols)))
    basis = np.zeros_like(quantity)
    cash = np.zeros(len(grid))
    cash[0] = plan["model"]["capital"]
    positions = {}
    counts = dict.fromkeys(("entry", "exit", "funding"), 0)
    phase = {"funding": 0, "exit": 1, "entry": 2}
    previous = (grid[0], -1)
    raw_pnl = paid = slippage = funding_net = 0.0
    funding_seen = set()
    for event in read(path / "events.json"):
        kind, symbol, ident, side, qty = (
            event[k] for k in ("type", "symbol", "position_id", "side", "quantity")
        )
        clock = pd.Timestamp(event["time"])
        effective = pd.Timestamp(event.get("effective_time", event["time"]))
        order = (effective, phase[kind])
        if order < previous or qty <= 0 or side not in (-1, 1):
            raise ValueError("Invalid event sequence")
        previous = order
        counts[kind] += 1
        i, col = grid.get_loc(effective), index[symbol]
        old = positions.get(ident)
        if old and (old["symbol"], old["side"]) != (symbol, side):
            raise ValueError("Position identity changed")
        if kind == "funding":
            if not (
                effective - STEP < clock <= effective
                and old
                and math.isclose(qty, old["quantity"], abs_tol=1e-10)
                and (symbol, clock) not in funding_seen
            ):
                raise ValueError("Funding did not use pre-fill inventory and exact clock")
            source = data[symbol]["funding"].loc[clock]
            delta = -side * qty * source["mark"] * source["funding_rate"]
            funding_net += delta
            funding_seen.add((symbol, clock))
        else:
            submitted = pd.Timestamp(event["submitted_at"])
            if submitted + STEP > clock or (kind == "entry" and submitted + STEP != clock):
                raise ValueError("Fill leaked through its decision cutoff")
            bar = data[symbol]["bars"].loc[clock - STEP]
            sign = 1 if kind == "entry" else -1
            price = float(bar.close * (1 + side * sign * slip))
            charge = qty * price * fee
            if not math.isclose(price, event["price"], abs_tol=1e-10) or not math.isclose(
                charge, event["fee"], abs_tol=1e-10
            ):
                raise ValueError("Fill price/fee does not reconcile")
            if qty * price > bar.quote_volume * plan["model"]["participation"] + 1e-8:
                raise ValueError("Fill exceeds completed interval volume")
            raw_pnl -= sign * side * qty * bar.close
            paid += charge
            slippage += qty * abs(price - bar.close)
            quantity[i, col] += sign * side * qty
            if kind == "entry":
                if (old is not None) != event["addition"]:
                    raise ValueError("Addition flag differs")
                position = positions.setdefault(
                    ident,
                    {
                        "symbol": symbol,
                        "side": side,
                        "quantity": 0.0,
                        "basis": 0.0,
                    },
                )
                position["quantity"] += qty
                position["basis"] += qty * price
                basis[i, col] += side * qty * price
                delta = -charge
            else:
                if not old or qty > old["quantity"] + 1e-12:
                    raise ValueError("Exit lacks settled inventory")
                entry_price = old["basis"] / old["quantity"]
                basis[i, col] -= side * qty * entry_price
                old["quantity"] -= qty
                old["basis"] -= qty * entry_price
                delta = side * qty * (price - entry_price) - charge
                if old["quantity"] < 1e-12:
                    del positions[ident]
        if not math.isclose(event["cash_delta"], delta, abs_tol=1e-9):
            raise ValueError(f"Recorded cash differs: {event}")
        cash[i] += delta
    holdings = quantity.cumsum(axis=0)
    for symbol, item in data.items():
        times = item["funding"].index
        times = times[(times > grid[0]) & (times <= grid[-1])]
        before = grid.searchsorted(times, side="left") - 1
        present = np.abs(holdings[before, index[symbol]]) > 1e-10
        if {clock for clock in times[present]} != {
            clock for s, clock in funding_seen if s == symbol
        }:
            raise ValueError("Missing funded inventory event")
    marks = np.column_stack([data[s]["bars"].close.loc[grid - STEP] for s in symbols])
    net = (holdings * marks).sum(axis=1)
    gross = (np.abs(holdings) * marks).sum(axis=1)
    balance = cash.cumsum()
    nav = balance + net - basis.cumsum(axis=0).sum(axis=1) - gross * (fee + slip)
    maximum_error = max(
        float(np.max(np.abs(grid_frame[k] - v)))
        for k, v in (
            ("cash", balance),
            ("equity", nav),
            ("net_exposure", net),
            ("gross_exposure", gross),
        )
    )
    if maximum_error > 2e-7 or len(positions) != row["open_positions"]:
        raise ValueError("NAV or final positions failed reconciliation")
    if not positions and not math.isclose(
        raw_pnl - paid - slippage + funding_net, nav[-1] - nav[0], abs_tol=2e-7
    ):
        raise ValueError("Terminal economic attribution differs")
    return {
        "nav_observations": len(nav),
        "events": counts,
        "maximum_curve_error_usdt": maximum_error,
        "price_pnl_usdt_if_flat": float(raw_pnl) if not positions else None,
        "fees_usdt": float(paid),
        "slippage_usdt": float(slippage),
        "funding_usdt": float(funding_net),
    }


def main():
    plan = study.checked_plan()
    result = read(study.RESULT)
    if result["registration_sha256"] != sha(study.PLAN) or result["admission_sha256"] != sha(
        study.OUT / "admission.json"
    ):
        raise ValueError("Results differ from the registered intake")
    data = inputs.panel(plan)
    books = {}
    verified_signals = {}
    for name, row in result["accounts"].items():
        path = study.OUT / name
        for file, checksum in row["hashes"].items():
            if sha(path / file) != checksum:
                raise ValueError("Account evidence differs from original")
        key = (row["window"], row["case"])
        targets = row["hashes"]["targets.parquet"]
        if key not in verified_signals:
            books[name] = {
                "verified_scheduled_decisions": check_targets(data, path / "targets.parquet", row)
            }
            verified_signals[key] = targets
        else:
            if targets != verified_signals[key]:
                raise ValueError("Cost scenarios use different target schedules")
            books[name] = {}
        books[name].update(check_account(plan, data, path, row))
    save(
        Path(__file__).with_name("verification.json"),
        {
            "result_sha256": sha(study.RESULT),
            "registration_sha256": sha(study.PLAN),
            "accounts": books,
            "authenticated_execution": False,
        },
    )
    print("Independently verified", len(books), "accounts", flush=True)


if __name__ == "__main__":
    main()
