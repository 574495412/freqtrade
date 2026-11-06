"""Independently reconstruct modeled cash, inventory, NAV and execution bounds."""

import math
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/breadth_diffusion_20261107_v001"
STEP = pd.Timedelta(minutes=5)


def audit_account(plan, panel, name):  # noqa: C901 -- explicit event accounting
    path = OUT / name
    result = pd.read_parquet(path / "nav.parquet").set_index("date")
    grid = result.index
    expected = pd.date_range(plan["start"], plan["end"], freq="5min", tz="UTC")
    if not grid.equals(expected):
        raise ValueError("account calendar changed")
    fee = plan["fee_per_side"]
    slip = (int(name.rsplit("_", 1)[1].removesuffix("bps")) / 10000) - fee
    symbols = plan["symbols"]
    index = {symbol: i for i, symbol in enumerate(symbols)}
    quantity = np.zeros((len(grid), len(symbols)))
    cost_basis = np.zeros_like(quantity)
    cash = np.zeros(len(grid))
    cash[0] = plan["capital_usdt"]
    open_positions = {}
    counts = dict.fromkeys(("entry", "exit", "funding"), 0)
    prior_time = grid[0]
    total_absolute_cash_error = 0.0
    funding_total = fees_total = slip_total = price_pnl = 0.0
    for event in read(path / "events.json"):
        kind, symbol, side = (event[k] for k in ("type", "symbol", "side"))
        qty = event["quantity"]
        ident = event["position_id"]
        time = pd.Timestamp(event["time"])
        if time < prior_time or qty <= 0 or side not in (-1, 1):
            raise ValueError("invalid event order or quantity")
        prior_time = time
        counts[kind] += 1
        col = index[symbol]
        mark_data, funding_data = panel[symbol]
        if kind == "funding":
            i = grid.get_loc(time + STEP)
            p = open_positions[ident]
            source = funding_data.loc[time]
            if not (
                p["symbol"] == symbol
                and p["side"] == side
                and math.isclose(qty, p["quantity"], abs_tol=1e-12)
            ):
                raise ValueError("funding inventory mismatch")
            delta = -side * qty * source["mark"] * source["funding_rate"]
            funding_total += delta
        else:
            i = grid.get_loc(time)
            submitted = pd.Timestamp(event["submitted_at"])
            if submitted + STEP > time or (kind == "entry" and submitted + STEP != time):
                raise ValueError("execution leaked through decision cutoff")
            bar = mark_data.loc[time - STEP]
            fill_price = bar.close * (1 + (side if kind == "entry" else -side) * slip)
            if not math.isclose(event["price"], fill_price, abs_tol=1e-12):
                raise ValueError("fill price differs from close/cost contract")
            if (
                qty * fill_price
                > bar.quote_volume * plan["execution"]["realized_interval_participation"] + 1e-8
            ):
                raise ValueError("fill exceeds interval volume")
            charge = qty * fill_price * fee
            fees_total += charge
            slip_total += qty * bar.close * slip
            price_pnl += (-1 if kind == "entry" else 1) * side * qty * bar.close
            if not math.isclose(charge, event["fee"], abs_tol=1e-9):
                raise ValueError("fee does not reconcile")
            if kind == "entry":
                if ident in open_positions or any(
                    p["symbol"] == symbol for p in open_positions.values()
                ):
                    raise ValueError("duplicate or overlapping symbol inventory")
                open_positions[ident] = {
                    "symbol": symbol,
                    "side": side,
                    "quantity": qty,
                    "entry_price": fill_price,
                }
                quantity[i, col] += side * qty
                cost_basis[i, col] += side * qty * fill_price
                delta = -charge
            else:
                p = open_positions[ident]
                if not (p["symbol"] == symbol and p["side"] == side and qty <= p["quantity"]):
                    raise ValueError("exit inventory mismatch")
                basis = p["entry_price"]
                quantity[i, col] -= side * qty
                cost_basis[i, col] -= side * qty * basis
                delta = side * qty * (fill_price - basis) - charge
                p["quantity"] -= qty
                if p["quantity"] < 1e-12:
                    del open_positions[ident]
        total_absolute_cash_error += abs(delta - event["cash_delta"])
        cash[i] += delta

    inventory = quantity.cumsum(axis=0)
    values = np.column_stack([panel[s][0].close.loc[grid - STEP].to_numpy() for s in symbols])
    gross = (np.abs(inventory) * values).sum(axis=1)
    expected_cash = cash.cumsum()
    net = expected_cash + (inventory * values - cost_basis.cumsum(axis=0)).sum(axis=1)
    net -= gross * (fee + slip)
    errors = {
        key: float(np.max(np.abs(value - result[key].to_numpy())))
        for key, value in (("cash", expected_cash), ("equity", net), ("gross_exposure", gross))
    }
    if max(*errors.values(), total_absolute_cash_error) > 2e-7:
        raise ValueError(f"account mismatch: {errors}")
    if len(read(path / "state.json")["positions"]) != len(open_positions):
        raise ValueError("terminal inventory differs")
    threshold = result.equity.lt(
        result.equity.cummax() * (1 - plan["risk"]["account_drawdown_halt"])
    )
    if not open_positions and not math.isclose(
        price_pnl + funding_total - fees_total - slip_total,
        net[-1] - plan["capital_usdt"],
        abs_tol=2e-7,
    ):
        raise ValueError("terminal attribution differs")
    return {
        "nav_points": len(grid),
        "first_drawdown_threshold_breach_utc": grid[threshold][0].isoformat()
        if threshold.any()
        else None,
        "fixed_fill_path_attribution": {
            "pre_cost_price_pnl_usdt": float(price_pnl) if not open_positions else None,
            "modeled_slippage_usdt": float(slip_total),
            "fees_usdt": float(fees_total),
            "funding_usdt": float(funding_total),
        },
        "events": counts,
        "maximum_curve_error_usdt": errors,
        "aggregate_event_cash_error_usdt": total_absolute_cash_error,
        "sha256": {p.name: sha(p) for p in sorted(path.iterdir())},
    }


def main():
    registration = HERE / "registration.json"
    plan = read(registration)
    results = HERE / "result.json"
    if read(results)["registration_sha256"] != sha(registration):
        raise ValueError("registration mismatch")
    data = OUT / "data"
    for name, checksum in read(data / "receipts.json").items():
        if sha(data / name) != checksum:
            raise ValueError("sealed data changed")
    panel = {
        s: (
            pd.read_parquet(data / f"{s}_5m.parquet"),
            pd.read_parquet(data / f"{s}_funding.parquet"),
        )
        for s in plan["symbols"]
    }
    receipt = {}
    for name in read(results)["cost_results"]:
        receipt[name] = audit_account(plan, panel, name)
    save(
        HERE / "verification.json",
        {
            "registration_sha256": sha(registration),
            "result_sha256": sha(results),
            "independent_account_code_sha256": sha(Path(__file__)),
            "data_quality_receipt_sha256": sha(data / "quality.json"),
            "data_hash_index_sha256": sha(data / "receipts.json"),
            "accounts": receipt,
            "passed": True,
            "authenticated_execution": False,
        },
    )


if __name__ == "__main__":
    main()
