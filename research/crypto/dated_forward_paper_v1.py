"""One preregistered public-book cash-and-carry paper entry, never broker orders.

Decimal cash and integer base inventory; the two legs fill independently. This
bounded observation cannot certify a complete carry lifecycle, margin safety,
continuous drawdown, account permissions, commissions, or future execution.
"""

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal


D = Decimal
UNIT = D("0.00000001")


def floor(value, step):
    return (D(value) / D(step)).to_integral_value(rounding=ROUND_FLOOR) * D(step)


def atoms(value):
    raw = D(value) / UNIT
    if raw != raw.to_integral_value():
        raise ValueError("Inventory exceeds registered precision")
    return int(raw)


def filters(symbol):
    rules = symbol["filters"]
    if len({r["filterType"] for r in rules}) != len(rules):
        raise ValueError("Duplicate filters")
    return {r["filterType"]: r for r in rules}


def quantity_ok(quantity, symbol):
    quantity = D(quantity)
    for name in ["LOT_SIZE", "MARKET_LOT_SIZE"]:
        rule = filters(symbol)[name]
        low, high, step = (D(rule[k]) for k in ["minQty", "maxQty", "stepSize"])
        if (low and quantity < low) or (high and quantity > high) or (step and quantity % step):
            return False
    return quantity > 0


def notional_ok(quantity, symbol, reference):
    rules, value = filters(symbol), D(quantity) * D(reference)
    if "NOTIONAL" in rules:
        r = rules["NOTIONAL"]
        if r["applyMinToMarket"] and value < D(r["minNotional"]):
            return False
        if r["applyMaxToMarket"] and value > D(r["maxNotional"]):
            return False
    if "MIN_NOTIONAL" in rules:
        r = rules["MIN_NOTIONAL"]
        if r.get("applyToMarket", True) and value < D(r.get("notional", r.get("minNotional"))):
            return False
    return True


def sweep(book, side, requested, config, *, reference=None, execution_rules=()):
    """Cap modeled fills to a fraction of displayed depth inside a fixed band.

    Adverse price stress is a cash-model charge on top of observed VWAP. It
    does not claim a matching-engine fill at an invented tick price.
    """
    levels = book["asks" if side == "buy" else "bids"]
    levels = sorted([(D(p), D(q)) for p, q in levels], reverse=side == "sell")
    if not levels or D(requested) <= 0:
        return {"quantity": D(0), "quote": D(0), "levels": []}
    top, remaining, quote, used = levels[0][0], D(requested), D(0), []
    band, participation = D(config["maximum_book_walk_fraction"]), D(config["depth_fraction"])
    for price, available in levels:
        if (side == "buy" and price > top * (1 + band)) or (
            side == "sell" and price < top * (1 - band)
        ):
            break
        for rule in execution_rules:
            if rule["ruleType"] != "PRICE_RANGE":
                raise ValueError("Unknown execution rule")
            if reference is not None:
                prefix = "bid" if side == "buy" else "ask"
                if (
                    not D(reference) * D(rule[prefix + "LimitMultDown"])
                    <= price
                    <= D(reference) * D(rule[prefix + "LimitMultUp"])
                ):
                    return {"quantity": D(requested) - remaining, "quote": quote, "levels": used}
        take = min(remaining, available * participation)
        if take > 0:
            used.append({"price": price, "quantity": take})
            quote += take * price
            remaining -= take
        if remaining == 0:
            break
    return {"quantity": D(requested) - remaining, "quote": quote, "levels": used}


def modeled_fill(
    book, side, quantity, symbol, config, reference, execution_rules=(), *, execution_reference=None
):
    if not quantity_ok(quantity, symbol) or not notional_ok(quantity, symbol, reference):
        return {"quantity": D(0), "quote": D(0), "reason": "request_filter_rejection"}
    found = sweep(
        book, side, quantity, config, reference=execution_reference, execution_rules=execution_rules
    )
    # Exchange partial fills have matching-engine granularity unavailable in a
    # public book. Model only a conservative whole reported lot; retain the rest.
    step = D(filters(symbol)["LOT_SIZE"]["stepSize"])
    actual = floor(found["quantity"], step)
    if not actual or not quantity_ok(actual, symbol):
        return {"quantity": D(0), "quote": D(0), "reason": "insufficient_model_depth"}
    consumed = sweep(
        book, side, actual, config, reference=execution_reference, execution_rules=execution_rules
    )
    stress = D(config["adverse_price_fraction"])
    quote = consumed["quote"] * (1 + stress if side == "buy" else 1 - stress)
    return {
        "quantity": actual,
        "quote": quote,
        "raw_quote": consumed["quote"],
        "stress_charge": abs(quote - consumed["quote"]),
        "reason": "full" if actual == D(quantity) else "partial",
    }


def target(spot_book, future_book, spot_symbol, future_symbol, config):
    fee = D(config["spot_base_fee_fraction"])
    spot_step = D(filters(spot_symbol)["LOT_SIZE"]["stepSize"])
    future_step = D(filters(future_symbol)["LOT_SIZE"]["stepSize"])
    spot_top = D(spot_book["asks"][0][0])
    cap = D(config["capital"]) * D(config["spot_fraction"]) / D(config["asset_count"])
    gross_cap = floor(cap / (spot_top * (1 + D(config["adverse_price_fraction"]))), spot_step)
    # Only reported depth inside the precommitted band; no post-result resizing.
    spot_available = sweep(spot_book, "buy", gross_cap, config)["quantity"]
    future_available = sweep(future_book, "sell", gross_cap, config)["quantity"]
    maximum = min(floor(spot_available, spot_step) * (1 - fee), future_available)
    maximum = min(maximum, D(filters(future_symbol)["MARKET_LOT_SIZE"]["maxQty"]))
    short = floor(maximum, future_step)
    if short <= 0:
        return {"gross": D(0), "short": D(0), "cash_cap": cap}
    gross = (short / ((1 - fee) * spot_step)).to_integral_value(rounding=ROUND_CEILING) * spot_step
    if gross > gross_cap:
        raise ValueError("Rounded target exceeds cash cap")
    return {"gross": gross, "short": short, "cash_cap": cap}


def economic_screen(gross, short, spot_fill, future_fill, config):
    """Stress terminal-cost comparison, not locked profit or a return forecast.

    Matched spot/short price terms cancel at common delivery price. Remaining
    terminal fees depend on that unknown price. Charge those at the registered
    3x spot entry VWAP scenario; residual long receives zero terminal credit.
    """
    gross, short = D(gross), D(short)
    if not gross or not short or spot_fill["quantity"] != gross or future_fill["quantity"] != short:
        return {"eligible": False, "reason": "decision_quantity_or_depth_insufficient"}
    spot_price = spot_fill["quote"] / gross
    future_price = future_fill["quote"] / short
    terminal_price = spot_price * D(config["terminal_price_cost_stress_multiple"])
    terminal_cost = (
        short
        * terminal_price
        * (
            1
            - (1 - D(config["adverse_price_fraction"])) * (1 - D(config["spot_exit_fee_fraction"]))
            + D(config["settlement_fee_fraction"])
        )
    )
    entry_future_fee = future_fill["quote"] * D(config["future_fee_fraction"])
    gross_basis_cash = short * future_price - spot_fill["quote"]
    net = gross_basis_cash - entry_future_fee - terminal_cost
    return {
        "eligible": net >= D(config["minimum_useful_pnl_per_asset_usdt"]),
        "reason": "useful_cost_stress_passed"
        if net >= D(config["minimum_useful_pnl_per_asset_usdt"])
        else "below_useful_cost_stress_floor",
        "gross_basis_cash": gross_basis_cash,
        "entry_future_fee": entry_future_fee,
        "terminal_cost_scenario": terminal_cost,
        "net_terminal_scenario": net,
        "terminal_price_scenario": terminal_price,
        "future_entry_price": future_price,
        "spot_entry_price": spot_price,
        "future_profit_or_margin_safety_proven": False,
    }


def _mark_account(state, observations, config):
    value, floating, reserve = D(0), D(0), D(0)
    valuation_complete = True
    for asset, position in state["positions"].items():
        obs = observations[asset]
        if not all(obs["book_valid"]["mark"].values()):
            valuation_complete = False
            position["valuation_unknown"] = True
            continue
        spot, short = D(position["spot_atoms"]) * UNIT, D(position["short_atoms"]) * UNIT
        spot_bid, future_ask = (
            D(obs["mark"]["spot"]["bids"][0][0]),
            D(obs["mark"]["future"]["asks"][0][0]),
        )
        value += spot * spot_bid
        floating += position["future_entry_quote"] - short * future_ask
        sellable = floor(spot, filters(obs["symbols"]["spot"])["LOT_SIZE"]["stepSize"])
        if not quantity_ok(sellable, obs["symbols"]["spot"]) or not notional_ok(
            sellable, obs["symbols"]["spot"], obs["spot_notional_reference"]
        ):
            sellable = D(0)
        dust = spot - sellable
        dust_reserve = dust * spot_bid
        exit_reserve = (
            sellable
            * spot_bid
            * (
                1
                - (1 - D(config["adverse_price_fraction"]))
                * (1 - D(config["spot_exit_fee_fraction"]))
            )
        )
        exit_reserve += (
            short
            * future_ask
            * (
                (1 + D(config["adverse_price_fraction"])) * (1 + D(config["future_fee_fraction"]))
                - 1
            )
        )
        reserve += dust_reserve + exit_reserve
        position.update(
            owned_unliquidatable_atoms=atoms(dust),
            dust_reserve=dust_reserve,
            exit_reserve=exit_reserve,
            mark_ns=obs["mark_ns"],
        )
    state["observations"].append(
        {
            "time_ns": max(o["mark_ns"] for o in observations.values()),
            "quote_cash": state["quote_cash"],
            "future_cash": state["future_cash"],
            "spot_value": value if valuation_complete else None,
            "future_unrealized": floating if valuation_complete else None,
            "noncash_exit_reserve": reserve if valuation_complete else None,
            "equity_after_exit_reserve": (
                state["quote_cash"] + state["future_cash"] + value + floating - reserve
            )
            if valuation_complete
            else None,
        }
    )


def run(observations, config):
    state = {
        "quote_cash": D(config["capital"]) * (1 - D(config["future_wallet_fraction"])),
        "future_cash": D(config["capital"]) * D(config["future_wallet_fraction"]),
        "positions": {},
        "events": [],
        "decisions": [],
        "observations": [],
        "cash_control": D(config["capital"]),
        "complete_lifecycles": 0,
        "win_rate": None,
        "orders_enabled": False,
    }
    for asset, obs in observations.items():
        symbol_s, symbol_f = obs["symbols"]["spot"], obs["symbols"]["future"]
        current = target(
            obs["decision"]["spot"], obs["decision"]["future"], symbol_s, symbol_f, config
        )
        d_spot = modeled_fill(
            obs["decision"]["spot"],
            "buy",
            current["gross"],
            symbol_s,
            config,
            obs["spot_notional_reference"],
            obs["execution_rules"],
            execution_reference=obs["spot_execution_reference"],
        )
        d_future = modeled_fill(
            obs["decision"]["future"],
            "sell",
            current["short"],
            symbol_f,
            config,
            obs["future_notional_reference"],
        )
        screen = economic_screen(current["gross"], current["short"], d_spot, d_future, config)
        state["decisions"].append(
            {"asset": asset, "time_ns": obs["decision_ns"], "target": current, "screen": screen}
        )
        decision_valid = obs["public_inputs_valid"] and all(obs["book_valid"]["decision"].values())
        if not decision_valid or not screen["eligible"]:
            state["events"].append(
                {
                    "asset": asset,
                    "type": "stay_cash",
                    "time_ns": obs["decision_ns"],
                    "reason": screen["reason"] if decision_valid else "public_constraints_unknown",
                }
            )
            continue
        position = {
            "spot_atoms": 0,
            "short_atoms": 0,
            "future_entry_quote": D(0),
            "state": "entry_pending",
        }
        state["positions"][asset] = position
        # Independent hypothetical orders; neither fill is borrowed from its
        # other leg or from the decision book. No claim of atomic execution.
        for market, side, q, sym, ref, rules in [
            (
                "spot",
                "buy",
                current["gross"],
                symbol_s,
                obs["spot_notional_reference"],
                obs["execution_rules"],
            ),
            ("future", "sell", current["short"], symbol_f, obs["future_notional_reference"], []),
        ]:
            fill = modeled_fill(
                obs["arrival"][market],
                side,
                q,
                sym,
                config,
                ref,
                rules,
                execution_reference=obs["spot_execution_reference"] if market == "spot" else None,
            )
            if not obs["book_valid"]["arrival"][market]:
                fill = {"quantity": D(0), "quote": D(0), "reason": "arrival_book_unknown"}
            if market == "spot":
                net = fill["quantity"] * (1 - D(config["spot_base_fee_fraction"]))
                if fill["quote"] > state["quote_cash"]:
                    raise ValueError("Spot wallet underfunded")
                state["quote_cash"] -= fill["quote"]
                position["spot_atoms"] += atoms(net)
                base_fee = fill["quantity"] - net
                fee_quote = D(0)
            else:
                fee_quote = fill["quote"] * D(config["future_fee_fraction"])
                state["future_cash"] -= fee_quote
                position["short_atoms"] += atoms(fill["quantity"])
                position["future_entry_quote"] += fill["quote"]
                base_fee = D(0)
            state["events"].append(
                {
                    "asset": asset,
                    "market": market,
                    "type": "paper_entry",
                    "time_ns": obs["arrival_ns"],
                    "side": side,
                    "requested": q,
                    "fill": fill,
                    "base_fee": base_fee,
                    "quote_fee": fee_quote,
                }
            )
        complete = position["spot_atoms"] == atoms(
            current["gross"] * (1 - D(config["spot_base_fee_fraction"]))
        ) and position["short_atoms"] == atoms(current["short"])
        position["state"] = "held_to_registered_exit" if complete else "emergency_unwind"
        if not complete:
            for market, side, quantity, sym, ref, rules in [
                (
                    "spot",
                    "sell",
                    floor(
                        D(position["spot_atoms"]) * UNIT, filters(symbol_s)["LOT_SIZE"]["stepSize"]
                    ),
                    symbol_s,
                    obs["spot_notional_reference"],
                    obs["execution_rules"],
                ),
                (
                    "future",
                    "buy",
                    D(position["short_atoms"]) * UNIT,
                    symbol_f,
                    obs["future_notional_reference"],
                    [],
                ),
            ]:
                fill = modeled_fill(
                    obs["unwind"][market],
                    side,
                    quantity,
                    sym,
                    config,
                    ref,
                    rules,
                    execution_reference=obs["spot_execution_reference"]
                    if market == "spot"
                    else None,
                )
                if not obs["book_valid"]["unwind"][market]:
                    fill = {"quantity": D(0), "quote": D(0), "reason": "unwind_book_unknown"}
                fee = fill["quote"] * D(
                    config["spot_exit_fee_fraction"]
                    if market == "spot"
                    else config["future_fee_fraction"]
                )
                if market == "spot":
                    position["spot_atoms"] -= atoms(fill["quantity"])
                    state["quote_cash"] += fill["quote"] - fee
                else:
                    before = D(position["short_atoms"]) * UNIT
                    released = (
                        position["future_entry_quote"] * fill["quantity"] / before
                        if before
                        else D(0)
                    )
                    state["future_cash"] += released - fill["quote"] - fee
                    position["future_entry_quote"] -= released
                    position["short_atoms"] -= atoms(fill["quantity"])
                state["events"].append(
                    {
                        "asset": asset,
                        "market": market,
                        "type": "paper_emergency",
                        "time_ns": obs["unwind_ns"],
                        "fill": fill,
                        "quote_fee": fee,
                    }
                )
            position["state"] = (
                "failed_entry_owned_inventory"
                if position["spot_atoms"] or position["short_atoms"]
                else "failed_entry_flat"
            )
    _mark_account(state, observations, config)
    if min(state["quote_cash"], state["future_cash"]) < 0:
        raise ValueError("Negative paper wallet")
    state.update(
        capture_completed=True,
        continuous_risk_monitoring=False,
        lifecycle_validation_completed=False,
        profit_goal_met=False,
        actual_order_eligibility_certified=False,
    )
    return state
