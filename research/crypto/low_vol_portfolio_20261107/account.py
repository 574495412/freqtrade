"""Five-minute mark and cash replay for a weekly, signed futures basket."""

import numpy as np
import pandas as pd

from research.crypto.nfi_strength_allocation_20261107.register import record
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked


CAPITAL = 1000.0


def post_cost_target(equity, old, target, prices, fee):
    low, high = 0.0, equity
    for _ in range(56):
        mid = (low + high) / 2
        if mid + fee * np.abs(target * mid - old).sum() > equity:
            high = mid
        else:
            low = mid
    return target * low / prices


def snapshot(data, pairs):
    markets = [data["markets"][p] for p in pairs]
    tiers = [data["leverage_tiers"][p][0] for p in pairs]
    if any(t["minNotional"] != 0 or t["maintAmt"] != 0 or t["maxLeverage"] < 2 for t in tiers):
        raise ValueError("Unsupported first maintenance tier")
    return {
        "lot": np.array([m["precision"]["amount"] for m in markets]),
        "minimum": np.array([m["limits"]["cost"]["min"] for m in markets]),
        "market_max": np.array([m["limits"]["market"]["max"] for m in markets]),
        "tier_max": np.array([t["maxNotional"] for t in tiers]),
        "maintenance": np.array([t["maintenanceMarginRate"] for t in tiers]),
    }


def make_inputs(grid, raw, funded, admission, symbol_pair):
    opens, marks, flows, unknown, capacity = {}, {}, {}, {}, {}
    for symbol, frame in raw.items():
        opens[symbol] = frame.open.reindex(grid)
        capacity[symbol] = frame.volume.shift(1).rolling(12).mean().reindex(grid) * 0.01
        path = checked(admission[symbol_pair(symbol)]["frame"])
        marks[symbol] = pd.read_parquet(path).open.reindex(grid)
        events, gaps, _ = funded[symbol]
        flows[symbol] = events.reindex(grid, fill_value=0)
        unknown[symbol] = grid.isin(gaps)
    matrices = {
        key: pd.DataFrame(values, index=grid).to_numpy()
        for key, values in (
            ("price", opens),
            ("mark", marks),
            ("fund", flows),
            ("unknown", unknown),
            ("capacity", capacity),
        )
    }
    if any(not np.isfinite(arr).all() for arr in matrices.values()) or (
        (matrices["price"] <= 0).any() or (matrices["mark"] <= 0).any()
    ):
        raise ValueError("Incomplete valuation or execution input")
    return matrices


def order_batch(cash, quantity, price, target, fee, limits, capacity):
    equity = cash + quantity @ price
    if equity <= 0:
        return None
    wanted = post_cost_target(equity, quantity * price, target, price, fee)
    lots = limits["lot"]
    desired = np.sign(wanted) * np.floor(np.abs(wanted) / lots + 1e-10) * lots
    delta = desired - quantity
    # A full reduce-only close is allowed below minimum notional in this model.
    small = (np.abs(delta) * price < limits["minimum"]) & (desired != 0)
    desired[small] = quantity[small]
    delta = desired - quantity
    charge = float(np.abs(delta) @ price * fee)
    if (np.abs(delta) > np.minimum(capacity, limits["market_max"]) + 1e-9).any() or (
        np.abs(desired) @ price / 2 > equity - charge + 1e-9
    ):
        return None
    return desired


def independently_reconcile(grid, data, ledger, nav, fee):
    change = np.zeros_like(data["price"])
    for i, symbol, delta in ledger:
        change[i, symbol] = delta
    post = np.cumsum(change, axis=0)
    before = np.vstack((np.zeros((1, post.shape[1])), post[:-1]))
    costs = (np.abs(change) * data["price"]).sum(axis=1) * fee
    flow = -(before * data["fund"] + change * data["price"]).sum(axis=1) - costs
    cash = CAPITAL + np.cumsum(flow)
    reserve = fee * (np.abs(post) * data["mark"]).sum(axis=1)
    value = cash + (post * data["mark"]).sum(axis=1) - reserve
    error = float(np.max(np.abs(value - nav)))
    if error > 1e-7 or (np.abs(post[-1]) > 1e-10).any():
        raise ValueError(f"Independent portfolio cash parity failed: {error}")
    return error


def simulate(grid, data, ranks, limits, gross, cost, folder):
    fee = cost / 10000
    prices, marks, funds, unknown, capacity = (
        data[k] for k in ("price", "mark", "fund", "unknown", "capacity")
    )
    wallet = CAPITAL
    quantity = np.zeros(prices.shape[1])
    peak = CAPITAL
    halted = close_pending = False
    margin_breaches = missing = rejected = over_capacity_closes = 0
    cost_cash = long_funding = short_funding = 0.0
    gross_max = 0.0
    nav = np.empty(len(grid))
    ledger = []
    rebalance = {i: ranks[at] for i, at in enumerate(grid) if at in ranks}
    for i in range(len(grid)):
        funding = -quantity * funds[i]
        wallet += float(funding.sum())
        long_funding += float(funding[quantity > 0].sum())
        short_funding += float(funding[quantity < 0].sum())
        missing += int(np.sum(unknown[i] & (quantity != 0)))
        mark = marks[i]
        notional = np.abs(quantity) * mark
        value = wallet + quantity @ mark
        current = value - notional.sum() * fee
        peak = max(peak, current)
        if (notional > limits["tier_max"]).any():
            raise ValueError("Position exceeded admitted first-tier range")
        margin_breach = bool(notional.sum() and current <= notional @ limits["maintenance"])
        margin_breaches += margin_breach
        desired = None
        if close_pending or i == len(grid) - 1:
            desired = np.zeros_like(quantity)
            over_capacity_closes += int(
                (np.abs(quantity) > np.minimum(capacity[i], limits["market_max"]) + 1e-9).any()
            )
        elif not halted and not margin_breach and current >= 0.70 * peak and i in rebalance:
            desired = order_batch(
                wallet, quantity, prices[i], gross * rebalance[i], fee, limits, capacity[i]
            )
            if desired is None:
                rejected += 1
        if not halted and (margin_breach or current < 0.70 * peak):
            halted = True
            close_pending = True
        elif close_pending:
            close_pending = False
        if desired is not None:
            delta = desired - quantity
            charge = float(np.abs(delta) @ prices[i] * fee)
            wallet -= float(delta @ prices[i]) + charge
            cost_cash += charge
            ledger.extend(
                (i, int(j), float(delta[j])) for j in np.flatnonzero(np.abs(delta) > 1e-12)
            )
            quantity = desired
            notional = np.abs(quantity) * mark
            current = wallet + quantity @ mark - fee * notional.sum()
        nav[i] = current
        peak = max(peak, current)
        if not halted and i < len(grid) - 1 and current < 0.70 * peak:
            halted = close_pending = True
        if current > 0:
            gross_max = max(gross_max, float(notional.sum() / current))

    error = independently_reconcile(grid, data, ledger, nav, fee)
    folder.mkdir()
    curve = folder / "nav.parquet"
    pd.DataFrame({"time": grid, "equity": nav}).to_parquet(curve, index=False)
    events = folder / "ledger.parquet"
    pd.DataFrame(ledger, columns=["row", "column", "delta"]).to_parquet(events, index=False)
    returns = nav[-1] / CAPITAL - 1
    years = (grid[-1] - grid[0]).days / 365
    peak_curve = np.maximum.accumulate(np.r_[CAPITAL, nav])[1:]
    return {
        "net": float(returns),
        "annualized": float((nav[-1] / CAPITAL) ** (1 / years) - 1) if nav[-1] > 0 else None,
        "drawdown": float(np.max(1 - nav / peak_curve)),
        "funding_long": long_funding,
        "funding_short": short_funding,
        "cost_usdt": cost_cash,
        "marked_max_gross_ratio": gross_max,
        "missing_held_funding": missing,
        "modeled_margin_breach_clocks": margin_breaches,
        "halted": halted,
        "rejected_batches": rejected,
        "over_capacity_closes": over_capacity_closes,
        "points": len(grid),
        "trades": len(ledger),
        "independent_equity_error": error,
        "curve": record(curve),
        "ledger": record(events),
    }


def assess(report):
    checks = {}
    for window, rows in report.items():
        for cost in (13, 25):
            one, two = (rows[f"{window}_{n}x_{cost}bps"] for n in (1, 2))
            net = two["net"]
            dd = two["drawdown"]
            checks[f"{window}_{cost}bps"] = {
                "funding": one["missing_held_funding"] == two["missing_held_funding"] == 0,
                "risk": not two["halted"]
                and not two["modeled_margin_breach_clocks"]
                and dd <= 0.25,
                "capacity": not (two["rejected_batches"] or two["over_capacity_closes"]),
                "return": net > 0 and two["annualized"] is not None and two["annualized"] >= 0.25,
                "increment": net > 0 and net >= 1.5 * one["net"],
                "risk_adjusted": net * one["drawdown"] >= one["net"] * dd,
            }
    return checks
