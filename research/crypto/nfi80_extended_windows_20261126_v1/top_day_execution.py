"""Use Binance public futures aggTrades to audit four native entry clocks."""

import csv
from hashlib import sha256
from io import TextIOWrapper
from itertools import chain
from math import fsum, isclose
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .annual_archives import archive_checksum, public_blob, store_raw
from .continuous_native_inputs import IndependentShardRead
from .inputs import HERE, OUT
from .native_trade_path_review import books, timestamp


SOURCE = "https://data.binance.vision/data/futures/um/daily/aggTrades"
STORE = OUT / "selected_top_days_execution"


def capture(symbol, clock):
    name = f"{symbol}-aggTrades-{clock.date().isoformat()}.zip"
    url = f"{SOURCE}/{symbol}/{name}"
    archive = STORE / name
    checksum = archive.with_suffix(".zip.CHECKSUM")
    if not checksum.exists():
        data = public_blob(f"{url}.CHECKSUM")
        assert data is not None
        archive_checksum(data, name)
        store_raw(checksum, data)
    digest = archive_checksum(checksum.read_bytes(), name)
    if not archive.exists():
        data = public_blob(url)
        assert data is not None and len(data) < 64 * 1024 ** 2
        assert sha256(data).hexdigest() == digest
        store_raw(archive, data)
    return {"url": url, "archive": receipt(checked({"path": archive, "sha256": digest})),
            "published_checksum": receipt(checksum)}


def prints(reference, clock):
    start = clock.value // 1_000_000
    values = []
    with ZipFile(checked(reference["archive"])) as archive:
        filename, = archive.namelist()
        with archive.open(filename) as member, TextIOWrapper(member, encoding="utf-8") as stream:
            rows = csv.reader(stream)
            first = next(rows)
            if not first[0].isdigit():
                assert first == ["agg_trade_id", "price", "quantity", "first_trade_id", "last_trade_id",
                                 "transact_time", "is_buyer_maker"]
            else:
                rows = chain([first], rows)
            for _id, price, quantity, _first, _last, stamp, maker in rows:
                elapsed = int(stamp) - start
                if 0 <= elapsed < 300_000:
                    assert maker in ("true", "false", "True", "False")
                    values.append((elapsed, float(price), float(quantity), maker.lower() == "true"))
    result = pd.DataFrame(values, columns=["ms", "price", "amount", "seller_taker"])
    assert not result.empty and result.ms.is_monotonic_increasing
    assert (result[["price", "amount"]] > 0).all(axis=None) and np.isfinite(result[["price", "amount"]]).all(axis=None)
    return result


def buy_print_proxy(rows, qty, native_rate, latency_seconds):
    start = latency_seconds * 1000
    scope = rows.loc[rows.ms.ge(start) & rows.ms.lt(start + 5000) & ~rows.seller_taker]
    cum = scope.amount.cumsum().to_numpy()
    last = cum.searchsorted(qty)
    result = {"delay_seconds": latency_seconds, "observed_5_second_qty": float(scope.amount.sum())}
    if last == len(scope):
        return result | {"matched_print_amount": False}
    prior = scope.iloc[:last]
    value = fsum((prior.amount * prior.price).tolist()) + (qty - prior.amount.sum()) * scope.price.iloc[last]
    vwap = float(value / qty)
    return result | {"matched_print_amount": True, "printed_vwap": vwap,
                     "price_delta_bp": (vwap / native_rate - 1) * 10_000,
                     "entry_cost_delta_usdt": float(value - qty * native_rate)}


def measure(rows, order, tape):
    qty, rate = order["amount"], order["safe_price"]
    eligible = rows.loc[rows.seller_taker & rows.price.le(rate)]
    cum = eligible.amount.cumsum()
    reaches = eligible.loc[cum.ge(qty), "ms"]
    return {
        "original_quantity": qty, "original_buy_limit": rate, "original_entry_notional_usdt": qty * rate,
        "qty_fraction_of_prints_in_entry_5m": float(qty / rows.amount.sum()),
        "target_qty_as_fraction_of_executed_at_or_below_limit": (
            float(qty / eligible.amount.sum()) if not eligible.empty else None
        ),
        "first_observed_eligible_qty_ms": None if reaches.empty else int(reaches.iloc[0]),
        "observed_sell_taker_qty_at_or_below_limit": {
            str(t): float(eligible.loc[eligible.ms.lt(t * 1000), "amount"].sum()) for t in (1, 5, 30, 300)
        },
        "buy_print_price_proxies": [buy_print_proxy(rows, qty, rate, delay) for delay in (0, 1, 5, 15)],
        "independent_native_candle": {key: float(tape[key]) for key in ("open", "high", "low", "close", "volume")},
        "agg_trade_open": float(rows.price.iloc[0]),
        "same_native_and_print_open": isclose(rows.price.iloc[0], tape.open, rel_tol=1e-12, abs_tol=0),
    }


def main():
    policy = receipt(HERE / "top_day_execution_protocol.json")
    targets = read(checked(policy))["targets"]
    refs = {str(fee): receipt(HERE / f"recent_two_year_{fee}bp_14_verified.json") for fee in (15, 8)}
    freeze = write_once(HERE / "top_day_execution_freeze.json", {
        "source": receipt(Path(__file__)), "protocol": policy, "native_accounts": refs, "orders_enabled": False,
    })
    accounts = {fee: read(checked(ref))["account"] for fee, ref in refs.items()}
    plans = {fee: read(checked(account["registered_plan"])) for fee, account in accounts.items()}
    assert plans["15"]["data_receipts"] == plans["8"]["data_receipts"]
    data = IndependentShardRead(plans["15"]["data_receipts"])
    orders = {}
    for fee, account in accounts.items():
        cfg = read(checked(account["effective_config"]))
        assert cfg["order_types"]["entry"] == "limit"
        _, orders[fee] = books(read(checked(account["proof"])))
    evidence = []
    for row in targets:
        pair, clock = row["pair"], pd.Timestamp(row["clock"])
        symbol = pair.split("/")[0] + "USDT"
        archive = capture(symbol, clock)
        rows = prints(archive, clock)
        tape = data(pair, "5m-futures").set_index("date").loc[clock]
        figures = {}
        key = pair, False, clock
        for fee, book in orders.items():
            if key not in book:
                continue
            native = book[key]
            entry = native["orders"][0]
            assert entry["ft_is_entry"] and timestamp(entry["order_filled_timestamp"]) == clock
            figures[fee] = measure(rows, entry, tape) | {"whole_campaign_net_usdt": native["profit_abs"]}
        evidence.append({**row, "archive": archive, "observations_5m": len(rows), "measures_by_fee": figures})
        print(pair, clock, {fee: c["qty_fraction_of_prints_in_entry_5m"] for fee, c in figures.items()}, flush=True)
    write_once(HERE / "top_day_execution_result.json", {
        "freeze": freeze, "targets": evidence, "orders_enabled": False, "fills_verified_live": False,
    })


if __name__ == "__main__":
    main()
