"""Independent Decimal/Fraction readback; never a primary account simulator."""

import importlib.util
import json
from bisect import bisect_right
from copy import deepcopy
from datetime import datetime
from decimal import ROUND_HALF_UP
from decimal import Decimal as D
from fractions import Fraction
from pathlib import Path
from zipfile import ZipFile

import pandas as pd


HERE = Path(__file__).absolute().parent


def read(path):
    return json.loads(Path(path).read_text())


def dt(value):
    return datetime.fromisoformat(value)


def current_quantity(order, asof, events):
    q = Fraction(D(str(order["quantity"])))
    for event in events:
        if dt(order["at"]) < dt(event["effective_open"]) <= asof:
            q *= Fraction(event["new_shares"], event["old_shares"])
    return D(q.numerator) / D(q.denominator)


def financial_snapshot(snapshot):
    value = deepcopy(snapshot)
    for _, _, mark in value["holdings"]:
        # Full-source provenance intentionally changes when future raw quotes
        # change. Only the financial prefix, with exact clocks/prices, is causal.
        del mark["source_sha256"]
    return value


def main():
    oracle = HERE.with_name("start_lifecycle_native_20261115_v1") / "audit_trace.py"
    spec = importlib.util.spec_from_file_location("independent_fee_oracle", oracle)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    receipts = read(HERE / "execution_receipt.json")
    assert len(receipts) == 22 and all(r["returncode"] == 0 for r in receipts)
    traces, checked, fills, rejections, rows = {}, 0, 0, 0, []
    for receipt in receipts:
        name = receipt["case"]
        directory = HERE / "runs" / name
        if name.endswith("fractional_reject"):
            rejection = read(directory / "expected_rejection.json")
            assert rejection["expected"] and not rejection["completed"]
            assert rejection["trade_amount"] == 6 and rejection["stake_amount"] == 600
            assert len(rejection["orders"]) == 1
            assert not list((directory / "backtest_results").glob("*.zip"))
            rejections += 1
            continue
        record = read(directory / "native_trace.json")
        traces[name] = record
        prices = pd.read_feather(directory / "synthetic_ohlcv.feather").set_index("date")
        orders, events = record["orders"], record["split_contract"]["events"]
        assert len(orders) == 3
        fills += len(orders)
        settlement = record["cash_contract"]["settlement_days"]
        labels = [s["label"] for s in settlement]
        lag = 1 if record["market"] == "XNYS" else 2
        for kind in ["open", "close"]:
            for i, snapshot in enumerate(record[kind + "s"]):
                at = dt(snapshot["at"])
                cash, shares, fees, locked = D(1000), D(0), D(0), D(0)
                paid, receivable, count = D(0), D(0), 0
                for order in orders:
                    filled = dt(order["at"])
                    if filled > at or (kind == "open" and filled == at):
                        continue
                    count += 1
                    quantity, price = D(str(order["quantity"])), D(str(order["price"]))
                    assert quantity == quantity.to_integral_value()
                    assert price == D(str(prices.loc[pd.Timestamp(filled).normalize(), "open"]))
                    charge = old.fee_for(order, record["fee_contract"])
                    fees += charge
                    units = current_quantity(order, at, events)
                    if order["side"] == "buy":
                        cash -= quantity * price + charge
                        shares += units
                    else:
                        proceeds = quantity * price - charge
                        cash += proceeds
                        shares -= units
                        release = settlement[
                            bisect_right(labels, filled.date().isoformat()) + lag - 1
                        ]
                        if dt(release["available_at"]) > at:
                            locked += proceeds
                for event in record["dividend_contract"]["events"]:
                    ex = dt(event["ex_open"])
                    if ex > at:
                        continue
                    entitled = sum(
                        (
                            current_quantity(o, ex, events) * (1 if o["side"] == "buy" else -1)
                            for o in orders
                            if dt(o["at"]) < ex
                        ),
                        D(0),
                    )
                    assert entitled >= 0
                    gross = (entitled * D(event["gross_per_share"])).quantize(
                        D(".01"), rounding=ROUND_HALF_UP
                    )
                    tax = (gross * D(event["withholding_rate"])).quantize(
                        D(".01"), rounding=ROUND_HALF_UP
                    )
                    if dt(event["payment_known_at"]) <= at and dt(event["available_at"]) <= at:
                        paid += gross - tax
                    else:
                        receivable += gross - tax
                cash += paid
                mark = D(str(prices.loc[pd.Timestamp(at).normalize(), kind]))
                expected = {
                    "total_cash": cash,
                    "free_cash": cash - locked,
                    "unsettled_receivable": locked,
                    "execution_fees": fees,
                    "market_value": shares * mark,
                    "net_nav": cash + shares * mark + receivable,
                    "paid_dividends": paid,
                    "dividend_receivable": receivable,
                }
                for key, value in expected.items():
                    assert abs(D(str(snapshot[key])) - value) < D("1e-8"), (name, kind, i, key)
                    checked += 1
                assert snapshot["filled_orders"] == count
                assert len(snapshot["holdings"]) == int(shares > 0)
                if shares:
                    assert D(str(snapshot["holdings"][0][1])) == shares
        # The historical filled-order records in the actual native ZIP must
        # keep their original units even when Trade's current units differ.
        archive = next((directory / "backtest_results").glob("*.zip"))
        with ZipFile(archive) as z:
            n = next(
                n for n in z.namelist() if n.endswith(".json") and not n.endswith("_config.json")
            )
            stats = json.loads(z.read(n))["strategy"]["OpenClockProbe"]
            exported = [o for t in stats["trades"] for o in t["orders"]]
            actual = [o for o in orders if not o["is_open"]]
            assert len(exported) == len(actual) == 2
            for e, a in zip(exported, actual, strict=True):
                assert e["amount"] == a["quantity"] and e["safe_price"] == a["price"]
                assert e["ft_order_side"] == a["side"]
                assert e["order_filled_timestamp"] == int(dt(a["at"]).timestamp() * 1000)
            gross = sum(
                D(str(o["quantity"])) * D(str(o["price"])) * (1 if o["side"] == "sell" else -1)
                for o in actual
            )
            assert abs(D(str(stats["profit_total_abs"])) - gross) < D("1e-8")
        terminal = next(t for t in record["trades"] if t["is_open"])
        original_buy = next(o for o in orders if o["trade_id"] == terminal["id"])
        basis = D(str(original_buy["quantity"])) * D(str(original_buy["price"]))
        assert abs(D(str(terminal["stake_amount"])) - basis) < D("1e-8")
        assert abs(D(str(terminal["open_rate"])) * D(str(terminal["amount"])) - basis) < D("1e-8")
        result = read(directory / "equity_net_result.json")
        snapshots = [
            s for pair in zip(record["opens"], record["closes"], strict=True) for s in pair
        ]
        values = [D(str(s["net_nav"])) for s in snapshots]
        peak, dd = values[0], D(0)
        for value in values:
            peak = max(peak, value)
            dd = max(dd, 1 - value / peak)
        assert abs(D(str(result["synthetic_net_change_ratio"])) - (values[-1] / values[0] - 1)) < D(
            "1e-12"
        )
        assert abs(D(str(result["synthetic_sampled_max_drawdown"])) - dd) < D("1e-12")
        rows.append(
            {
                "case": name,
                "passed": True,
                "terminal_quantity": terminal["amount"],
                "terminal_basis": terminal["stake_amount"],
                "native_closed_profit": str(gross),
            }
        )
    for market in ["XNYS", "XHKG"]:
        default, empty = traces[market + "_default"], traces[market + "_none"]
        parent = read(
            HERE.with_name("dividend_lifecycle_native_20261116_v1")
            / "runs"
            / (market + "_none")
            / "native_trace.json"
        )
        for key in ["opens", "closes", "orders", "callbacks"]:
            assert default[key] == empty[key] == parent[key], (market, key)
        a, b = traces[market + "_future2"], traces[market + "_future4"]
        for kind in ["opens", "closes"]:
            assert [financial_snapshot(s) for s in a[kind][:6]] == [
                financial_snapshot(s) for s in b[kind][:6]
            ]
        # Exact inverse split quotes preserve gross position value; fees and
        # later per-share dividends remain independently charged in new units.
        held = traces[market + "_forward_held"]
        before, after = held["closes"][4], held["opens"][5]
        assert empty["opens"][5]["market_value"] == after["market_value"]
        assert before["total_cash"] == after["total_cash"]
        assert empty["opens"][5]["net_nav"] == after["net_nav"]
    output = {
        "passed": True,
        "native_completed_starts": 20,
        "native_zip": 20,
        "expected_native_rejections": rejections,
        "completed_native_fills": fills,
        "partial_rejection_fills": 2,
        "native_boundaries": 280,
        "independent_scalar_checks": checked,
        "rows": rows,
        "source_or_candidate_accounting_imports": False,
        "historical_accounts": 0,
        "bench_records": 0,
    }
    (HERE / "independent_audit.json").write_text(json.dumps(output, indent=2) + "\n")
    print("Independent audit passed", checked, "scalars;20starts;2fractional rejections")


if __name__ == "__main__":
    main()
