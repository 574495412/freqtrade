"""Reconstruct independent marked equity, short economics and event chronology."""

import math
from collections import defaultdict
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.listing_reversal_20261107.catalog import HERE, OUT


MS = 300_000


def number(value):
    return int(pd.Timestamp(value).timestamp() * 1000) // MS


def digest(path, expected):
    if sha(path) != expected:
        raise ValueError(f"Sealed source differs: {path}")


def audit_sources():
    catalog = read(OUT / "catalog.json")
    raw = OUT / "catalog"
    for name, expected in catalog["metadata_sha256"].items():
        digest(raw / f"{name}.json", expected)
    for name, expected in catalog["page_sha256"].items():
        digest(raw / name, expected)
    for row in catalog["symbols"]:
        digest(raw / "first" / f"{row['symbol']}.xml", row["receipt_sha256"])

    panel = {}
    intake = read(OUT / "admission.json")
    for row in intake["rows"]:
        name = row["symbol"]
        path = OUT / "data" / name
        for item in row["archive_receipts"]:
            if item["state"] == "VERIFIED":
                source = path / item["file"]
                digest(source, item["sha256"])
                digest(source, source.with_suffix(".zip.CHECKSUM").read_text().split()[0])
        if row["state"] != "ADMITTED":
            continue
        digest(path / "funding.json", row["funding_response_sha256"])
        for family in ("trade", "mark", "funding"):
            digest(path / f"{family}.parquet", row[f"{family}_sha256"])
        trade = pd.read_parquet(path / "trade.parquet")
        mark = pd.read_parquet(path / "mark.parquet")
        funding = pd.read_parquet(path / "funding.parquet")
        expected = read(path / "funding.json")
        actual = list(
            zip(funding.index.as_unit("ms").asi8, funding.rate, funding.mark, strict=True)
        )
        if actual != [
            (r["fundingTime"], float(r["fundingRate"]), float(r["markPrice"])) for r in expected
        ]:
            raise ValueError(f"Funding translation mismatch for {name}")
        panel[name] = (trade, mark, funding, number(row["launch_utc"]))
    return panel


def assert_close(actual, expected, description):
    if not math.isclose(actual, expected, abs_tol=2e-8, rel_tol=1e-12):
        raise ValueError(f"{description} diverged: {actual}/{expected}")


def reconcile_symbol(  # noqa: C901 -- explicit native-time event branches
    name, entry_events, source, plan, case, slip, fee, curve, cash, exposure, count
):
    trade, mark, funding, launch = source
    if entry_events[0]["kind"] != "entry" or entry_events[-1]["kind"] not in (
        "exit",
        "collateral_loss",
    ):
        raise ValueError("Missing initial or terminal inventory event")
    entry = entry_events[0]
    start = entry["clock"]
    quantity = entry["qty"]
    if start != launch + 289:
        raise ValueError("Order did not use the first eligible fill clock")
    day = trade.iloc[:288]
    high = day.high.max()
    quote = day.quote_volume.sum()
    selected = high >= day.close.iloc[0] * (1 + plan["minimum_pump"]) and day.close.iloc[
        -1
    ] <= high * (1 - plan["minimum_pullback"])
    if quote < plan["day_quote_minimum"] or (case == "FailedLaunch" and not selected):
        raise ValueError(f"Entry lacked causal source signal for {name}")
    end = entry_events[-1]["clock"]
    position_events = defaultdict(list)
    for event in entry_events:
        position_events[event["clock"]].append(event)
    funding_map = defaultdict(list)
    for ts, row in funding.iterrows():
        if start * MS < ts.value // 1_000_000 <= end * MS:
            funding_map[number(ts.ceil("5min"))].append((ts.isoformat(), row.rate, row.mark))
    prior_stop = False
    pnl = 0.0
    first = curve.index[0]
    for tick in range(start, end + 1):
        i = tick - launch - 1
        raw = trade.iloc[i]
        events = position_events[tick]
        actual_funding = [e for e in events if e["kind"] == "funding"]
        expected_funding = funding_map[tick]
        if [(e["actual"], e["funding"], e["rate"]) for e in actual_funding] != expected_funding:
            raise ValueError("Missing, extra or mistimed funding")
        for event in events:
            kind = event["kind"]
            qty, rate = event["qty"], event["rate"]
            if kind == "entry":
                if event != entry:
                    raise ValueError("Duplicate entry")
                assert_close(rate, raw.close * (1 - slip), "entry execution")
                max_notional = min(raw.quote_volume, trade.quote_volume.iloc[i - 12 : i].mean())
                if qty * rate > max_notional * plan["volume_participation"] + 1e-8:
                    raise ValueError("Entry exceeded causal liquidity cap")
                delta = -qty * rate * fee
            elif kind == "funding":
                assert_close(qty, quantity, "funding inventory")
                delta = quantity * rate * event["funding"]
            elif kind == "collateral_loss":
                if mark.high.iloc[i] < entry["rate"] * (1 + plan["margin_loss_mark_increase"]):
                    raise ValueError("Collateral loss was not triggered")
                assert_close(qty, quantity, "catastrophe quantity")
                delta = -quantity * entry["rate"]
                quantity = 0.0
            elif kind == "exit":
                assert_close(rate, raw.close * (1 + slip), "exit execution")
                maximum = raw.quote_volume * plan["volume_participation"]
                if qty * rate > maximum + 1e-8 or qty > quantity + 1e-10:
                    raise ValueError("Exit exceeded availability")
                if tick <= start + plan["hold_hours"] * 12 and not prior_stop:
                    previous = curve.equity.loc[: tick - 1]
                    if not (
                        previous < previous.cummax() * (1 - plan["maximum_observed_drawdown"])
                    ).any():
                        raise ValueError("Exit preceded all registered triggers")
                delta = qty * (entry["rate"] - rate) - qty * rate * fee
                quantity -= qty
            else:
                raise ValueError(f"Unknown event: {kind}")
            assert_close(event["cash_delta"], delta, "event cash")
            cash[tick - first] += delta
            pnl += delta
        if tick > start and mark.high.iloc[i] >= entry["rate"] * (1 + plan["stop_mark_increase"]):
            prior_stop = True
        if quantity > 1e-10:
            exposure[tick - first] += quantity * (
                entry["rate"] - mark.close.iloc[i] * (1 + slip) * (1 + fee)
            )
            count[tick - first] += 1
    assert_close(quantity, 0, "terminal quantity")
    return pnl


def audit_account(plan, panel, key, result):
    case, suffix = key.rsplit("_", 1)
    fee = plan["fees_bps"] / 10000
    slip = (int(suffix.removesuffix("bps")) - plan["fees_bps"]) / 10000
    path = OUT / key
    curve = pd.read_parquet(path / "nav.parquet").set_index("clock")
    start = number(plan["common_start"] + "T00:00:00Z")
    end = number(plan["common_end"] + "T00:00:00Z")
    np.testing.assert_array_equal(curve.index, np.arange(start, end + 1))
    cash = np.zeros(len(curve))
    cash[0] = plan["capital"]
    exposure = np.zeros_like(cash)
    count = np.zeros(len(curve), dtype=int)
    by_name = defaultdict(list)
    events = read(path / "events.json")
    if any(a["clock"] > b["clock"] for a, b in pairwise(events)):
        raise ValueError("Nonchronological account ledger")
    for event in events:
        by_name[event["symbol"]].append(event)
    reconstructed = {
        name: reconcile_symbol(
            name, rows, panel[name], plan, case, slip, fee, curve, cash, exposure, count
        )
        for name, rows in by_name.items()
    }
    closed = read(path / "closed.json")
    if len(closed) != len(by_name) or len(closed) != result["completed"]:
        raise ValueError("Closed inventory count differs")
    for item in closed:
        assert_close(item["net_usdt"], reconstructed[item["symbol"]], "closed campaign")
    cash = cash.cumsum()
    equity = cash + exposure
    error = float(np.max(np.abs(equity - curve.equity)))
    if error > 2e-7 or np.max(np.abs(cash - curve.cash)) > 2e-7:
        raise ValueError(f"Saved NAV failed reconciliation: {error}")
    np.testing.assert_array_equal(count, curve.open_positions)
    if int(count.max()) > plan["maximum_slots"]:
        raise ValueError("Portfolio slot limit breached")
    assert_close(equity[-1] / equity[0] - 1, result["net_return"], "net return")
    assert_close(
        float((1 - equity / np.maximum.accumulate(equity)).max()), result["max_drawdown"], "MDD"
    )
    return {
        "nav_points": len(curve),
        "maximum_equity_error_usdt": error,
        "events": len(events),
        "campaigns": len(closed),
        "net_funding_usdt": math.fsum(e["cash_delta"] for e in events if e["kind"] == "funding"),
        "hashes": {x.name: sha(x) for x in sorted(path.iterdir())},
    }


def main():
    plan_path = HERE / "registration.json"
    result_path = HERE / "result.json"
    plan, result = read(plan_path), read(result_path)
    digest(plan_path, result["registration_sha256"])
    digest(OUT / "admission.json", result["admission_sha256"])
    panel = audit_sources()
    accounts = {
        name: audit_account(plan, panel, name, data) for name, data in result["accounts"].items()
    }
    save(
        HERE / "verification.json",
        {
            "registration_sha256": sha(plan_path),
            "result_sha256": sha(result_path),
            "auditor_sha256": sha(Path(__file__)),
            "verified_symbols": len(panel),
            "accounts": accounts,
            "model_execution_only": True,
        },
    )
    print({k: (v["nav_points"], v["maximum_equity_error_usdt"]) for k, v in accounts.items()})


if __name__ == "__main__":
    main()
