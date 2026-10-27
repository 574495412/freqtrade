"""Finite USDT-linear dated contracts over the segregated two-wallet research ledger.

Derived from frozen spot_perpetual_account_v2, retaining its execution/risk model.
Dated inventory settles once at its evidenced boundary; later source rows cannot
fill or mark old futures. Post-settlement spot unwind remains independently risky.
Explicit hypothetical grids retain fee-adjusted integer inventory and dust.
Trade-close/slippage/participation remain research proxies, not certified venue
filters, client commissions, matching latency or exchange liquidation.
"""

import hashlib
import json
from copy import deepcopy
from decimal import Decimal
from fractions import Fraction

import numpy as np
import pandas as pd


def utc(value):
    clock = pd.Timestamp(value)
    return clock.tz_localize("UTC") if clock.tzinfo is None else clock.tz_convert("UTC")


class Grid:
    """Execution quantity conversion only; upstream owns target-pair planning."""

    def __init__(self, rules, fee):
        if (
            rules.get("scope") != "hypothetical_grid_stress"
            or rules.get("historical_rules_certified") is not False
        ):
            raise ValueError("This version requires explicitly hypothetical quantity rules")
        self.unit = Fraction(str(rules["inventory_unit"]))
        if self.unit <= 0:
            raise ValueError("Invalid inventory unit")
        self.spot_step = self.atoms(rules["spot_step"])
        self.future_step = self.atoms(rules["future_base_step"])
        self.fee = Fraction(str(fee))
        if (
            min(self.spot_step, self.future_step) <= 0
            or (self.spot_step * (1 - self.fee)).denominator != 1
        ):
            raise ValueError("Quantity/fee unit cannot represent the assumed grid exactly")

    def atoms(self, quantity):
        value = Fraction(str(quantity)) / self.unit
        if value.denominator != 1 or value < 0:
            raise ValueError("Quantity outside exact inventory units")
        return value.numerator

    def floor(self, quantity, market):
        step = self.spot_step if market == "spot" else self.future_step
        return int(Fraction(str(max(0, quantity))) / self.unit // step) * step

    def text(self, atoms):
        return format(
            Decimal(atoms) * Decimal(self.unit.numerator) / Decimal(self.unit.denominator), "f"
        )

    def quantity(self, atoms):
        return float(self.text(atoms))

    def net(self, gross_atoms):
        net = gross_atoms * (1 - self.fee)
        if net.denominator != 1:
            raise ValueError("Fee cannot be represented without an undeclared rounding rule")
        return net.numerator

    def sync(self, position):
        for side in ["spot", "perp"]:
            position[side + "_q"] = self.quantity(position[side + "_atoms"])


def replay(  # noqa: C901
    spot,
    perpetual,
    marks,
    funding,
    signals,
    config,
    start,
    end,
    initial_state=None,
    final_end=None,
    contracts=None,
):
    begin, finish = utc(start), utc(end)
    terminal = utc(final_end) if final_end is not None else finish
    if finish > terminal or any(
        t.second or t.microsecond or t.minute % 5 for t in [begin, finish, terminal]
    ):
        raise ValueError("Invalid5m account boundary")
    symbols = sorted(spot)
    if (
        not symbols
        or set(symbols) != set(perpetual)
        or set(symbols) != set(marks)
        or set(symbols) != set(funding)
    ):
        raise ValueError("Market universes differ")
    if contracts is None or set(contracts) != set(symbols):
        raise ValueError("Explicit dated contract identity required")
    if any(len(f) for f in funding.values()):
        raise ValueError("Dated contract cannot receive perpetual funding")
    for s, c in contracts.items():
        if c["style"] != "linear" or c["quote"] != "USDT" or s != c["base"] + c["quote"]:
            raise ValueError("Only identity-matched USDT linear dated contracts supported")
        if not utc(c["known_at"]) <= begin < utc(c["expiry"]) and initial_state is None:
            raise ValueError("Initial decision outside declared known contract lifetime")
        if utc(c["entry_stop"]) >= utc(c["expiry"]):
            raise ValueError("Invalid reduce-only boundary")
        if not np.isfinite(c["settlement_price"]) or c["settlement_price"] <= 0:
            raise ValueError("Unknown settlement reference")
        if any(
            utc(c[k]).second or utc(c[k]).microsecond or utc(c[k]).minute % 5
            for k in ["expiry", "entry_stop"]
        ):
            raise ValueError("Contract boundaries must be actual5m boundaries")
    if (
        not 0 <= config["settlement_fee_bps"] < 10000
        or type(config.get("pretrade_liquidity_cap", False)) is not bool
    ):
        raise ValueError("Invalid declared settlement fee or liquidity policy")
    contract_identity = hashlib.sha256(json.dumps(contracts, sort_keys=True).encode()).hexdigest()
    capital = float(config["capital"])
    sf, pf, slip = (
        config["spot_fee_bps"] / 10000,
        config["perpetual_fee_bps"] / 10000,
        config["slippage_bps"] / 10000,
    )
    if (
        not 0 <= sf < 1
        or not 0 <= pf < 1
        or not 0 <= slip < 1
        or not 0 < config["participation"] <= 1
        or not 0 < config["initial_futures_wallet_fraction"] < 1
    ):
        raise ValueError("Invalid fees, participation or cash split")
    if config.get("unliquidatable_dust_reserve_fraction") != 1.0:
        raise ValueError("Unpriced dust disposal requires full noncash exit reserve")
    grids = {
        s: Grid(c["quantity_rules"], config["spot_fee_bps"] / 10000) for s, c in contracts.items()
    }
    clocks = pd.date_range(begin, finish, freq="5min")
    if len(clocks) < 2:
        raise ValueError("Empty account")
    times = clocks[:-1]
    columns = ["open", "high", "low", "close", "quote_volume"]
    arrays = {
        s: {
            "spot": spot[s].reindex(times)[columns].to_numpy(dtype=float, copy=True),
            "perp": perpetual[s].reindex(times)[columns].to_numpy(dtype=float, copy=True),
            "mark": marks[s]
            .reindex(times)[["open", "high", "low", "close"]]
            .to_numpy(dtype=float, copy=True),
        }
        for s in symbols
    }
    for s in symbols:
        inactive = times >= utc(contracts[s]["expiry"])
        for market in ["perp", "mark"]:
            arrays[s][market][inactive] = np.nan
    previous = {
        s: {
            "spot": float(spot[s].loc[begin - pd.Timedelta(minutes=5), "close"]),
            "perp": float(perpetual[s].loc[begin - pd.Timedelta(minutes=5), "close"])
            if begin - pd.Timedelta(minutes=5) < utc(contracts[s]["expiry"])
            else None,
            "mark": float(marks[s].loc[begin - pd.Timedelta(minutes=5), "close"])
            if begin - pd.Timedelta(minutes=5) < utc(contracts[s]["expiry"])
            else None,
        }
        for s in symbols
    }
    for s, asset in arrays.items():
        for market, a in asset.items():
            selected = a if market == "spot" else a[times < utc(contracts[s]["expiry"])]
            if not np.isfinite(selected).all():
                raise ValueError("Incomplete active market/mark data")
    decisions = {utc(r["time"]): r for r in signals}
    if len(decisions) != len(signals):
        raise ValueError("Duplicate decisions")
    state = deepcopy(initial_state) if initial_state else None
    if state and (
        state.get("engine_version") != "segregated_spot_dated.v2"
        or state.get("contracts_sha256") != contract_identity
        or state["next_clock"] != begin.isoformat()
        or state["config"] != config
        or state["symbols"] != symbols
        or state["terminal"] != terminal.isoformat()
        or state["previous_prices"] != previous
    ):
        raise ValueError("Continuation boundary, config, source marks or terminal differs")
    settled_contracts = deepcopy(state["settled_contracts"]) if state else {}
    if state and any(
        p["perp_q"] > 0 and begin >= utc(contracts[s]["expiry"])
        for s, p in state["positions"].items()
    ):
        raise ValueError("Expired inventory cannot survive its settlement checkpoint")
    wallet = deepcopy(state["wallets"]) if state else {"quote": capital, "futures": 0.0}
    positions = deepcopy(state["positions"]) if state else {}
    for symbol, p in positions.items():
        grid = grids[symbol]
        for side in ["spot", "perp"]:
            if (
                type(p[side + "_atoms"]) is not int
                or p[side + "_atoms"] < 0
                or p[side + "_q"] != grid.quantity(p[side + "_atoms"])
            ):
                raise ValueError("Checkpoint integer inventory and display quantity differ")
    rearm = deepcopy(state["rearm"]) if state else {}
    sequence = state["sequence"] if state else 0
    peak = state["peak"] if state else capital
    halted = state["risk_halted"] if state else False
    flags = (
        deepcopy(state["flags"])
        if state
        else {"possible_liquidation": False, "negative_wallet": False, "gross_over_one": False}
    )
    counters = (
        deepcopy(state["counters"])
        if state
        else {
            "entry_requests": 0,
            "hedge_failures": 0,
            "partial_exit_intervals": 0,
            "requested_notional": 0.0,
            "filled_notional": 0.0,
            "maximum_exit_delay_minutes": 0.0,
        }
    )
    events, trades, requests, curve = [], [], [], []
    funding_by_bar = {}
    for symbol, frame in funding.items():
        selected = frame.loc[(frame.index > begin) & (frame.index <= finish)]
        for t, row in selected.iterrows():
            i = int(clocks[1:].searchsorted(t, side="left"))
            funding_by_bar.setdefault(i, []).append(
                (t, symbol, float(row.funding_rate), float(row.mark))
            )
    for batch in funding_by_bar.values():
        batch.sort(key=lambda r: (r[0], r[1]))

    def price(s, market, i, field=3):
        if i < 0:
            return previous[s][market]
        if market != "spot" and times[i] >= utc(contracts[s]["expiry"]):
            return None
        value = float(arrays[s][market][i][field])
        if not np.isfinite(value):
            raise ValueError("Unknown owned price")
        return value

    def values(i, envelope=False):
        assets = floating = reserve = gross = net = notional = dust_value = base_delta = 0.0
        for s, p in positions.items():
            sp = price(s, "spot", i, 2 if envelope else 3)
            mp = price(s, "mark", i, 1 if envelope else 3) if p["perp_q"] else 0.0
            pp = price(s, "perp", i, 1 if envelope else 3) if p["perp_q"] else 0.0
            assets += p["spot_q"] * sp
            floating += p["perp_q"] * (p["perp_entry"] - mp)
            grid = grids[s]
            dust = grid.quantity(p["spot_atoms"] % grid.spot_step)
            dust_value += dust * sp
            base_delta += (p["spot_q"] - p["perp_q"]) * sp
            reserve += (
                (p["spot_q"] - dust) * sp * (sf + slip) + dust * sp + p["perp_q"] * pp * (pf + slip)
            )
            gross += p["spot_q"] * sp + p["perp_q"] * mp
            net += p["spot_q"] * sp - p["perp_q"] * mp
            notional += p["perp_q"] * mp
        return {
            "spot_assets": assets,
            "perp_unrealized": floating,
            "exit_reserve": reserve,
            "gross_notional": gross,
            "net_notional": net,
            "short_notional": notional,
            "unliquidatable_spot_value": dust_value,
            "base_delta_value": base_delta,
        }

    def nav(i):
        v = values(i)
        return (
            wallet["quote"]
            + wallet["futures"]
            + v["spot_assets"]
            + v["perp_unrealized"]
            - v["exit_reserve"]
        )

    def transfer(clock):
        if positions:
            raise ValueError("No hypothetical intrahold collateral transfer")
        desired = (wallet["quote"] + wallet["futures"]) * config["initial_futures_wallet_fraction"]
        delta = desired - wallet["futures"]
        if abs(delta) < 1e-12:
            return
        wallet["quote"] -= delta
        wallet["futures"] += delta
        events.append(
            {
                "type": "transfer",
                "time": clock.isoformat(),
                "effective_time": (
                    clock if not curve else clock + pd.Timedelta(minutes=5)
                ).isoformat(),
                "amount": delta,
                "quote_delta": -delta,
                "futures_delta": delta,
            }
        )

    def record(clock, i, margin_low=None, stress_nav=None):
        v = values(i)
        equity = (
            wallet["quote"]
            + wallet["futures"]
            + v["spot_assets"]
            + v["perp_unrealized"]
            - v["exit_reserve"]
        )
        curve.append(
            {
                "date": clock,
                "equity": equity,
                "quote_cash": wallet["quote"],
                "futures_wallet": wallet["futures"],
                **v,
                "margin_balance": wallet["futures"] + v["perp_unrealized"],
                "margin_envelope_balance": margin_low,
                "stress_nav_envelope": stress_nav,
                "risk_halted": halted,
            }
        )

    def request_exit(p, clock, reason, remaining=0.0):
        old = p.get("exit_request")
        if old is None or remaining < old["remaining_q"]:
            p["exit_request"] = {
                "submitted_at": clock.isoformat(),
                "reason": reason,
                "remaining_q": remaining,
            }
            requests.append(
                {
                    "time": clock.isoformat(),
                    "type": "exit",
                    "symbol": p["symbol"],
                    "position_id": p["id"],
                    "reason": reason,
                    "remaining_q": remaining,
                }
            )

    if state is None:
        transfer(begin)
    record(begin, -1)
    peak = max(peak, curve[-1]["equity"])
    for i, clock in enumerate(times):
        effective = clocks[i + 1]
        new_orders = []
        if clock in decisions and effective < terminal and not halted:
            if not positions:
                transfer(clock)
            for s in symbols:
                signal = decisions[clock]["assets"][s]
                if s in positions:
                    if signal["exit"]:
                        request_exit(
                            positions[s], clock, signal.get("exit_reason", "negative_known_funding")
                        )
                    continue
                if not signal["entry"] or signal["exit"] or (s in rearm and clock < utc(rearm[s])):
                    continue
                if utc(contracts[s]["entry_stop"]) <= effective:
                    requests.append(
                        {
                            "type": "entry_rejected",
                            "time": clock.isoformat(),
                            "symbol": s,
                            "reason": "reduce_only_or_expired",
                        }
                    )
                    continue
                if clock < utc(contracts[s]["known_at"]):
                    raise ValueError("Contract not known at entry")
                q = (
                    max(0.0, nav(i - 1))
                    / len(symbols)
                    * config["asset_spot_fraction"]
                    * (1 - sf)
                    / (price(s, "spot", i - 1) * (1 + config["entry_slippage_buffer_bps"] / 10000))
                )
                if config.get("pretrade_liquidity_cap", False):
                    sq = float(spot[s].loc[clock - pd.Timedelta(minutes=5), "quote_volume"])
                    fq = float(perpetual[s].loc[clock - pd.Timedelta(minutes=5), "quote_volume"])
                    q = min(
                        q,
                        max(0.0, sq)
                        * config["participation"]
                        * (1 - sf)
                        / (
                            price(s, "spot", i - 1)
                            * (1 + config["entry_slippage_buffer_bps"] / 10000)
                        ),
                        max(0.0, fq)
                        * config["participation"]
                        / (
                            price(s, "perp", i - 1)
                            * (1 + config["entry_slippage_buffer_bps"] / 10000)
                        ),
                    )
                if q <= 1e-14:
                    continue
                grid = grids[s]
                target = signal["lot_target"]
                gross_atoms = grid.atoms(target["gross_spot"])
                short_atoms = grid.atoms(target["short_base"])
                if gross_atoms % grid.spot_step or short_atoms % grid.future_step:
                    raise ValueError("Upstream target is not on the declared grid")
                if grid.quantity(gross_atoms) > q / (1 - sf) or grid.quantity(short_atoms) > q:
                    raise ValueError(
                        "Upstream lot target exceeds the unchanged past-known quantity cap"
                    )
                residual = grid.net(gross_atoms) - short_atoms
                if not 0 <= residual < grid.net(grid.spot_step):
                    raise ValueError("Upstream covering target has undeclared base exposure")
                if gross_atoms == 0 or short_atoms == 0:
                    continue
                new_orders.append(
                    {
                        "symbol": s,
                        "q": q,
                        "submitted_at": clock.isoformat(),
                        "gross_target_atoms": gross_atoms,
                        "short_target_atoms": short_atoms,
                        "planned_residual_atoms": residual,
                    }
                )
                counters["entry_requests"] += 1
                counters["requested_notional"] += q * (
                    price(s, "spot", i - 1) + price(s, "perp", i - 1)
                )
                requests.append(
                    {"type": "entry", "time": clock.isoformat(), "symbol": s, "quantity": q}
                )
        if effective == terminal:
            new_orders = []
            for p in positions.values():
                request_exit(p, clock, "scheduled_terminal")
        # Events are paid to inventory held before the closing-time fills.
        wallet_before_funding = wallet["futures"]
        negative_funding = 0.0
        for t, s, rate, mark in funding_by_bar.get(i, []):
            p = positions.get(s)
            if p is None or p["perp_q"] <= 0:
                continue
            cash = p["perp_q"] * mark * rate
            wallet["futures"] += cash
            p["cash_flow"] += cash
            negative_funding += min(0.0, cash)
            events.append(
                {
                    "type": "funding",
                    "time": t.isoformat(),
                    "effective_time": effective.isoformat(),
                    "symbol": s,
                    "position_id": p["id"],
                    "quantity": p["perp_q"],
                    "rate": rate,
                    "mark": mark,
                    "cash_delta": cash,
                }
            )
        old_envelope = values(i, envelope=True)
        margin_low = wallet_before_funding + negative_funding + old_envelope["perp_unrealized"]
        stress_nav = (
            wallet["quote"]
            + wallet_before_funding
            + negative_funding
            + old_envelope["spot_assets"]
            + old_envelope["perp_unrealized"]
            - old_envelope["exit_reserve"]
        )
        maintenance_breach = (
            old_envelope["short_notional"] > 0
            and margin_low <= config["maintenance_stress_ratio"] * old_envelope["short_notional"]
        )
        margin_warning = (
            old_envelope["short_notional"] > 0
            and margin_low <= config["margin_exit_ratio"] * old_envelope["short_notional"]
        )
        envelope_halt = peak > 0 and 1 - stress_nav / peak >= config["drawdown_halt"]
        for s, c in contracts.items():
            if effective == utc(c["expiry"]):
                if c["symbol"] in settled_contracts:
                    raise ValueError("Duplicate dated settlement")
                settled_contracts[c["symbol"]] = effective.isoformat()
                p = positions.get(s)
                if p is not None and p["perp_q"] > 0:
                    quantity = p["perp_q"]
                    settlement = float(c["settlement_price"])
                    fee = quantity * settlement * config["settlement_fee_bps"] / 10000
                    delta = quantity * (p["perp_entry"] - settlement) - fee
                    wallet["futures"] += delta
                    p["cash_flow"] += delta
                    p["fees"] += fee
                    p["perp_atoms"] = 0
                    grids[s].sync(p)
                    p["future_settled_at"] = effective.isoformat()
                    events.append(
                        {
                            "type": "settlement",
                            "market": "perp",
                            "symbol": s,
                            "contract": c["symbol"],
                            "position_id": p["id"],
                            "time": effective.isoformat(),
                            "quantity": quantity,
                            "quantity_atoms": grids[s].atoms(quantity),
                            "quantity_exact": grids[s].text(grids[s].atoms(quantity)),
                            "price": settlement,
                            "fee": fee,
                            "cash_delta": delta,
                            "notional": quantity * settlement,
                            "slippage_cost": 0.0,
                            "reason": "fixed_maturity_cash_settlement",
                        }
                    )
                    request_exit(p, effective, "post_settlement_spot_unwind")
        # Existing exits and original entries retain their prebar instruction;
        # a just-observed risk envelope cannot retroactively cancel/fill them.
        for s, p in list(positions.items()):
            order = p.get("exit_request")
            if order is None or utc(order["submitted_at"]) > clock:
                continue
            counters["maximum_exit_delay_minutes"] = max(
                counters["maximum_exit_delay_minutes"],
                (effective - utc(order["submitted_at"])).total_seconds() / 60,
            )
            remaining = order["remaining_q"]
            for market in ["spot", "perp"]:
                key = "spot_q" if market == "spot" else "perp_q"
                desired = max(0.0, p[key] - remaining)
                if desired <= 1e-14 or (
                    market == "perp" and effective >= utc(contracts[s]["expiry"])
                ):
                    continue
                direction = -1 if market == "spot" else 1
                px = price(s, market, i) * (1 + direction * slip)
                grid = grids[s]
                q_atoms = grid.floor(
                    min(desired, config["participation"] * arrays[s][market][i][4] / px), market
                )
                q_atoms = min(q_atoms, p["spot_atoms" if market == "spot" else "perp_atoms"])
                q = grid.quantity(q_atoms)
                if q_atoms == 0:
                    continue
                fee = q * px * (sf if market == "spot" else pf)
                delta = q * px - fee if market == "spot" else q * (p["perp_entry"] - px) - fee
                wallet["quote" if market == "spot" else "futures"] += delta
                p["cash_flow"] += delta
                p["spot_atoms" if market == "spot" else "perp_atoms"] -= q_atoms
                grid.sync(p)
                p["fees"] += fee
                p["slippage"] += q * abs(px - price(s, market, i))
                events.append(
                    {
                        "type": "exit",
                        "market": market,
                        "symbol": s,
                        "position_id": p["id"],
                        "time": effective.isoformat(),
                        "submitted_at": order["submitted_at"],
                        "reason": order["reason"],
                        "quantity": q,
                        "quantity_atoms": q_atoms,
                        "quantity_exact": grid.text(q_atoms),
                        "price": px,
                        "fee": fee,
                        "cash_delta": delta,
                        "notional": q * px,
                        "slippage_cost": q * abs(px - price(s, market, i)),
                    }
                )
            if (
                p["spot_atoms"] - p["perp_atoms"] != p["planned_residual_atoms"]
                and not p["hedge_failed"]
                and not (p.get("future_settled_at") and p["perp_q"] == 0)
            ):
                p["hedge_failed"] = True
                counters["hedge_failures"] += 1
                request_exit(p, effective, "partial_exit_hedge_failure")
            if (
                p["perp_atoms"] == 0
                and 0 < p["spot_atoms"] < grids[s].spot_step
                and not p.get("dust_first_at")
            ):
                p["dust_first_at"] = effective.isoformat()
                requests.append(
                    {
                        "type": "unresolved_grid_inventory",
                        "time": effective.isoformat(),
                        "symbol": s,
                        "position_id": p["id"],
                        "spot_atoms": p["spot_atoms"],
                        "quantity_exact": grids[s].text(p["spot_atoms"]),
                        "cash_booked": 0.0,
                    }
                )
            if p["spot_atoms"] == 0 and p["perp_atoms"] == 0:
                trades.append(
                    {
                        "symbol": s,
                        "position_id": p["id"],
                        "entry_time": p["entry_time"],
                        "exit_time": effective.isoformat(),
                        "net_pnl": p["cash_flow"],
                        "fees": p["fees"],
                        "slippage": p["slippage"],
                        "matched_hedge": not p["hedge_failed"] and p["planned_residual_atoms"] == 0,
                        "reason": order["reason"],
                    }
                )
                rearm[s] = (effective + pd.Timedelta(hours=config["rearm_hours"])).isoformat()
                del positions[s]
            elif max(p["spot_q"], p["perp_q"]) <= remaining + 1e-12:
                p.pop("exit_request", None)
            else:
                counters["partial_exit_intervals"] += 1
                counters["maximum_exit_delay_minutes"] = max(
                    counters["maximum_exit_delay_minutes"],
                    (effective - utc(order["submitted_at"])).total_seconds() / 60,
                )
        for order in new_orders:
            s, desired = order["symbol"], order["q"]
            sequence += 1
            p = {
                "id": sequence,
                "symbol": s,
                "entry_time": effective.isoformat(),
                "spot_q": 0.0,
                "perp_q": 0.0,
                "perp_entry": 0.0,
                "cash_flow": 0.0,
                "fees": 0.0,
                "slippage": 0.0,
                "hedge_failed": False,
                "spot_atoms": 0,
                "perp_atoms": 0,
                "planned_residual_atoms": order["planned_residual_atoms"],
                "target_gross_atoms": order["gross_target_atoms"],
                "target_short_atoms": order["short_target_atoms"],
            }
            sp = price(s, "spot", i) * (1 + slip)
            sq = max(
                0.0,
                min(
                    desired,
                    config["participation"] * arrays[s]["spot"][i][4] / sp * (1 - sf),
                    wallet["quote"] / sp * (1 - sf),
                ),
            )
            pp = price(s, "perp", i) * (1 - slip)
            existing = values(i)
            available_margin = (
                wallet["futures"]
                + existing["perp_unrealized"]
                - config["initial_margin_ratio"] * existing["short_notional"]
            )
            pq = max(
                0.0,
                min(
                    desired,
                    config["participation"] * arrays[s]["perp"][i][4] / pp,
                    available_margin
                    / (config["initial_margin_ratio"] * price(s, "mark", i) + pp * pf),
                ),
            )
            grid = grids[s]
            gross_atoms = min(order["gross_target_atoms"], grid.floor(sq / (1 - sf), "spot"))
            sq_atoms = grid.net(gross_atoms)
            pq_atoms = min(order["short_target_atoms"], grid.floor(pq, "perp"))
            sq, pq = grid.quantity(sq_atoms), grid.quantity(pq_atoms)
            if sq_atoms == 0 and pq_atoms == 0:
                continue
            positions[s] = p
            for market, q, px in [("spot", sq, sp), ("perp", pq, pp)]:
                if q <= 1e-14:
                    continue
                quantity_atoms = sq_atoms if market == "spot" else pq_atoms
                actual_gross_atoms = gross_atoms if market == "spot" else pq_atoms
                gross_q = grid.quantity(actual_gross_atoms)
                fee = (
                    grid.quantity(actual_gross_atoms - quantity_atoms) * px
                    if market == "spot"
                    else q * px * pf
                )
                delta = -gross_q * px if market == "spot" else -fee
                wallet["quote" if market == "spot" else "futures"] += delta
                p["spot_atoms" if market == "spot" else "perp_atoms"] = quantity_atoms
                grid.sync(p)
                if market == "perp":
                    p["perp_entry"] = px
                p["cash_flow"] += delta
                p["fees"] += fee
                p["slippage"] += gross_q * abs(px - price(s, market, i))
                counters["filled_notional"] += gross_q * px
                events.append(
                    {
                        "type": "entry",
                        "market": market,
                        "symbol": s,
                        "position_id": p["id"],
                        "time": effective.isoformat(),
                        "submitted_at": order["submitted_at"],
                        "quantity": q,
                        "gross_quantity": gross_q,
                        "quantity_atoms": quantity_atoms,
                        "quantity_exact": grid.text(quantity_atoms),
                        "gross_quantity_atoms": actual_gross_atoms,
                        "gross_quantity_exact": grid.text(actual_gross_atoms),
                        "price": px,
                        "fee": fee,
                        "cash_delta": delta,
                        "notional": gross_q * px,
                        "slippage_cost": gross_q * abs(px - price(s, market, i)),
                    }
                )
            if (
                sq_atoms != grid.net(order["gross_target_atoms"])
                or pq_atoms != order["short_target_atoms"]
            ):
                p["hedge_failed"] = True
                counters["hedge_failures"] += 1
                request_exit(p, effective, "entry_hedge_failure")
        flags["possible_liquidation"] |= bool(maintenance_breach)
        flags["negative_wallet"] |= bool(min(wallet.values()) < -1e-8)
        current_nav = nav(i)
        peak = max(peak, current_nav)
        flags["gross_over_one"] |= bool(values(i)["gross_notional"] > max(current_nav, 0) + 1e-8)
        if (
            maintenance_breach
            or margin_warning
            or envelope_halt
            or current_nav <= 0
            or 1 - current_nav / peak >= config["drawdown_halt"]
            or flags["negative_wallet"]
            or flags["gross_over_one"]
        ):
            halted = True
            for p in positions.values():
                request_exit(p, effective, "margin_or_drawdown_guard")
        elif (
            positions
            and values(i)["gross_notional"] > config["gross_reduction_trigger"] * current_nav
        ):
            ratio = config["gross_reduction_target"] * current_nav / values(i)["gross_notional"]
            for p in positions.values():
                request_exit(
                    p, effective, "gross_drift_reduction", min(p["spot_q"], p["perp_q"]) * ratio
                )
        record(effective, i, margin_low, stress_nav)
    return {
        "curve": pd.DataFrame(curve),
        "events": events,
        "trades": trades,
        "requests": requests,
        "positions": deepcopy(positions),
        "wallets": deepcopy(wallet),
        "risk_halted": halted,
        "flags": flags,
        "capacity": counters,
        "engine": "segregated_spot_dated_integer_inventory_full_dust_reserve_conditional.v2",
        "historical_venue_rules_certified": False,
        "resume_state": {
            "engine_version": "segregated_spot_dated.v2",
            "contracts_sha256": contract_identity,
            "settled_contracts": deepcopy(settled_contracts),
            "config": deepcopy(config),
            "symbols": symbols,
            "terminal": terminal.isoformat(),
            "next_clock": finish.isoformat(),
            "wallets": deepcopy(wallet),
            "positions": deepcopy(positions),
            "rearm": deepcopy(rearm),
            "sequence": sequence,
            "peak": peak,
            "risk_halted": halted,
            "flags": deepcopy(flags),
            "counters": deepcopy(counters),
            "previous_prices": {
                s: {m: price(s, m, len(times) - 1) for m in ["spot", "perp", "mark"]}
                for s in symbols
            },
        },
    }
