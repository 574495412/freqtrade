"""Independent exact-rational audit of actual native option orders and ZIPs.

Imports no candidate wallet, strategy, fee or margin implementation. Inputs are
the captured external synthetic marks, declared contracts and actual native
order histories. This is supporting reconciliation, never a trading engine.
"""

import json
import zipfile
from datetime import datetime
from fractions import Fraction
from pathlib import Path


HERE = Path(__file__).resolve().parent
F = Fraction


def load(path):
    return json.loads(Path(path).read_text())


def clock(value):
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        raise AssertionError("Audit requires aware actual clocks")
    return at


def fee(contract, quantity, price):
    return quantity * min(F(contract["trade_fee_rate"]), price * F(contract["premium_fee_cap"]))


def margin(spec, quantity, mark):
    if quantity >= 0:
        return F(0), F(0)
    strike, index, premium = (
        F(spec["strike_usd_per_base"]),
        F(mark["index_usd"]),
        F(mark["premium"]),
    )
    if spec["option_type"] == "call":
        otm = max((strike - index) / index, F(0))
        im = max(F(".15") - otm, F(".1")) + premium
        mm = F(".075") + premium
    else:
        otm = max(index - strike, F(0)) / index
        mm = max(F(".075"), F(".075") * premium) + premium
        im = max(max(F(".15") - otm, F(".1")) + premium, mm)
    return -quantity * im, -quantity * mm


def canonical(order):
    return (
        order["trade_id"],
        order["pair"],
        order["is_short"],
        order["at"],
        order["side"],
        order["quantity"],
        order["price"],
        order["status"],
    )


def boundary(contract, config, snap, history):
    state = snap["state"]
    at = clock(state["at"])
    assert clock(contract["starts"]) <= at <= clock(contract["ends"])
    specs = {s["instrument_name"] + "/" + s["base_currency"]: s for s in contract["specs"]}
    marks = {}
    for mark in snap["input_marks"]:
        pair = mark["pair"]
        assert pair in specs and pair not in marks
        assert clock(mark["at"]) <= clock(mark["known_at"]) <= at
        assert 0 <= (at - clock(mark["at"])).total_seconds() <= contract["max_mark_age"]
        assert mark["source_sha256"] == contract["source_sha256"]
        assert F(mark["index_usd"]) > 0 and F(mark["premium"]) >= 0
        marks[pair] = mark
    fills, pending, seen = {}, [], set()
    for order in snap["native_orders"]:
        key = order["pair"], order["order_id"]
        assert key not in seen and key[0] in specs and key[1]
        seen.add(key)
        assert clock(order["created_at"]) <= at
        quantity, price = F(order["quantity"]), F(order["price"])
        assert quantity >= 0 and price > 0 and order["side"] in {"buy", "sell"}
        if quantity:
            assert clock(order["at"]) <= at and not order["ft_is_open"]
            assert order["status"] == "closed" and quantity == F(order["requested_quantity"])
            assert quantity >= F(specs[key[0]]["min_amount_base"])
            fills[key] = canonical(order)
        elif order["ft_is_open"]:
            pending.append(order)
    assert all(fills.get(key) == old for key, old in history.items())
    flow = charges = F(0)
    positions, by_trade = {}, {}
    for _key, row in sorted(fills.items(), key=lambda item: (clock(item[1][3]), int(item[0][1]))):
        identity, pair, is_short, _, side, quantity, price, _ = row
        quantity, price = F(quantity), F(price)
        signed = quantity if side == "buy" else -quantity
        flow -= signed * price
        charges += fee(contract, quantity, price)
        by_trade[identity] = by_trade.get(identity, F(0)) + signed
        assert by_trade[identity] <= 0 if is_short else by_trade[identity] >= 0
        positions[pair] = positions.get(pair, F(0)) + signed
    positions = {pair: quantity for pair, quantity in positions.items() if quantity}
    value = initial = maintenance = reserve = F(0)
    for pair, quantity in positions.items():
        assert pair in marks
        value += quantity * F(marks[pair]["premium"])
        im, mm = margin(specs[pair], quantity, marks[pair])
        initial, maintenance = initial + im, maintenance + mm
    for order in pending:
        pair = order["pair"]
        entry_side = "sell" if order["is_short"] else "buy"
        if order["side"] != entry_side:
            continue
        quantity, price = F(order["requested_quantity"]), F(order["price"])
        if order["is_short"]:
            im, _ = margin(specs[pair], -quantity, marks[pair])
            reserve += im + fee(contract, quantity, price)
        else:
            reserve += quantity * price + fee(contract, quantity, price)
    capital = F(str(config["dry_run_wallet"]))
    cash = capital + flow - charges
    expected = {
        "initial_cash": capital,
        "premium_cash_flow": flow,
        "execution_fees": charges,
        "cash_balance": cash,
        "option_value": value,
        "equity": cash + value,
        "margin_balance": cash,
        "initial_margin": initial,
        "maintenance_margin": maintenance,
        "pending_reserve": reserve,
        "available_funds": cash - initial - reserve,
    }
    for field, number in expected.items():
        assert F(state[field]) == number, (field, state[field], str(number))
    assert state["currency"] == contract["currency"]
    assert {p: F(q) for p, q in state["positions"]} == positions
    assert state["initial_margin_breach"] == (cash < initial + reserve)
    assert state["liquidation_required"] == (cash < maintenance)
    return fills, len(expected)


def zip_audit(trace):
    with zipfile.ZipFile(trace["actual_zip"]) as archive:
        name = next(
            n for n in archive.namelist() if n.endswith(".json") and not n.endswith("_config.json")
        )
        payload = json.loads(archive.read(name))
        assert len(payload["strategy"]) == 1
        result = next(iter(payload["strategy"].values()))
    assert result["total_trades"] == 2 and len(result["trades"]) == 2
    actual = trace["snapshots"][-1]["native_orders"]
    gross = F(0)
    for trade in result["trades"]:
        assert not trade["is_open"] and trade["is_short"]
        assert trade["fee_open"] == trade["fee_close"] == trade["funding_fees"] == 0
        selected = [o for o in actual if o["pair"] == trade["pair"] and F(o["quantity"])]
        assert len(selected) == len(trade["orders"]) == 2
        for order in trade["orders"]:
            matches = [
                o
                for o in selected
                if o["side"] == order["ft_order_side"]
                and F(o["quantity"]) == F(str(order["amount"]))
                and F(o["price"]) == F(str(order["safe_price"]))
                and int(clock(o["at"]).timestamp() * 1000) == order["order_filled_timestamp"]
            ]
            assert len(matches) == 1
        gain = F(str(trade["amount"])) * (F(str(trade["open_rate"])) - F(str(trade["close_rate"])))
        assert F(str(trade["profit_abs"])) == gain
        gross += gain
    last = trace["snapshots"][-1]["state"]
    assert F(str(result["profit_total_abs"])) == gross == F(last["premium_cash_flow"])
    assert F(str(result["final_balance"])) == F(last["cash_balance"])
    assert F(last["cash_balance"]) - F(last["initial_cash"]) == gross - F(last["execution_fees"])
    return {
        "closed_trades": 2,
        "zip_fills": 4,
        "gross_trade_profit": str(float(gross)),
        "net_coin_cash_change": str(float(gross - F(last["execution_fees"]))),
        "zip_final_balance_is_net_cash_but_trade_profit_is_gross": True,
        "raw_zip_warmup_start": result["backtest_start"],
        "actual_first_capital_boundary": trace["snapshots"][0]["at"],
        "strategy_return": None,
    }


def case_audit(folder):
    trace = load(folder / "native_trace.json")
    contract = load(folder / "option_contract.json")
    config = load(folder / "config.json")
    assert trace["synthetic_only"] and trace["historical_option_accounts"] == 0
    assert trace["strategy_net_return"] is None
    history, scalars = {}, 0
    for snap in trace["snapshots"]:
        history, count = boundary(contract, config, snap, history)
        scalars += count
    assert len(history) == trace["actual_fill_count"]
    final = trace["snapshots"][-1]["state"]
    name = trace["case"]
    checks = {}
    if name == "long":
        before, after = [trace["snapshots"][i]["state"] for i in [1, 2]]
        assert before["cash_balance"] == after["cash_balance"]
        assert before["available_funds"] == after["available_funds"]
        assert F(after["equity"]) > F(before["equity"])
        assert final["positions"] and trace["actual_fill_count"] == 3
        checks["long_mark_gain_adds_no_cash_or_buying_power"] = True
    elif name == "short":
        after = trace["snapshots"][1]["state"]
        assert F(after["equity"]) == F(after["initial_cash"]) - F(after["execution_fees"])
        assert not final["positions"] and trace["actual_fill_count"] == 5
        checks["short_premium_credit_not_equity_profit_and_partial_close_counted_once"] = True
    elif name == "breach":
        assert final["liquidation_required"] and F(final["available_funds"]) < 0
        assert trace["actual_fill_count"] == 1 and not trace["liquidation_executed"]
        checks["margin_breach_retained_without_invented_liquidation"] = True
    elif name == "pending":
        middle = trace["snapshots"][1]["state"]
        assert F(middle["pending_reserve"]) > 0 and F(final["pending_reserve"]) == 0
        assert middle["cash_balance"] == final["cash_balance"] == final["initial_cash"]
        assert trace["actual_fill_count"] == 0
        checks["unfilled_premium_not_credited_native_cancel_releases_reserve"] = True
    else:
        assert name == "full_start" and trace["native_backtest_start"]
        assert len(trace["snapshots"]) == 16 and not final["positions"]
        checks["actual_zip"] = zip_audit(trace)
    return {
        "case": name,
        "currency": trace["currency"],
        "boundaries": len(trace["snapshots"]),
        "exact_coin_scalars": scalars,
        "actual_fills": len(history),
        "checks": checks,
    }


def main():
    execution = load(HERE / "execution_receipt.json")
    assert len(execution) == 10 and all(r["returncode"] == 0 for r in execution)
    results = [case_audit(Path(row["folder"])) for row in execution]
    result = {
        "status": "passed",
        "independent_candidate_imports": False,
        "arithmetic": "Exact rational from actual native quantities/prices and external marks",
        "results": results,
        "boundaries": sum(r["boundaries"] for r in results),
        "exact_coin_scalars": sum(r["exact_coin_scalars"] for r in results),
        "actual_fills": sum(r["actual_fills"] for r in results),
        "full_start_zips": sum(r["case"] == "full_start" for r in results),
        "historical_option_accounts": 0,
        "strategy_net_return": None,
    }
    (HERE / "independent_audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        "AUDIT PASS",
        result["actual_fills"],
        "fills;",
        result["boundaries"],
        "boundaries;",
        result["exact_coin_scalars"],
        "exact coin scalars",
        flush=True,
    )


if __name__ == "__main__":
    main()
