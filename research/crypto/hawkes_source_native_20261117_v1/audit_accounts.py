"""Actual exported-fill NAV/funding audit; no candidate or account-engine import."""

import hashlib
import json
import math
from decimal import Decimal as D
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(p):
    return json.loads(Path(p).read_text())


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(p, r):
    Path(p).write_text(json.dumps(r, indent=2, allow_nan=False) + "\n")


def metrics(nav):
    assert np.isfinite(nav).all() and (nav > 0).all()
    daily = (
        nav.reindex(pd.date_range(nav.index[0], nav.index[-1], freq="24h"))
        .pct_change(fill_method=None)
        .dropna()
    )
    sd = daily.std(ddof=1)
    years = (nav.index[-1] - nav.index[0]).total_seconds() / (365 * 86400)
    return dict(
        net_return=float(nav.iloc[-1] / nav.iloc[0] - 1),
        initial_usdt=float(nav.iloc[0]),
        ending_usdt=float(nav.iloc[-1]),
        cagr_365day=float((nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1),
        hourly_mark_drawdown=float((1 - nav / nav.cummax()).max()),
        daily_nav_sharpe=float(daily.mean() / sd * math.sqrt(365)) if sd > 0 else None,
        complete_24h_returns=len(daily),
        calendar_hours=len(nav),
    )


def periods(nav, plan):
    return [
        dict(name=p["name"], **metrics(nav.loc[p["start"] : p["end"]])) for p in plan["periods"]
    ]


def native_stats(d, strategy):
    paths = list((d / "native_results").glob("*.zip"))
    assert len(paths) == 1
    with ZipFile(paths[0]) as z:
        name = next(
            n for n in z.namelist() if n.endswith(".json") and not n.endswith("_config.json")
        )
        return json.loads(z.read(name))["strategy"][strategy], paths[0]


def funding_path(trade, event_times, unit_fees, clocks, policy):
    """Independent fill-derived single-entry funding, before-hour snapshots.

    All clocks are integer milliseconds. No engine or strategy code is used.
    Event fees enter a snapshot only after the event time, because snapshots
    are observed before that hour's native events/orders.
    """
    assert policy in ("before_orders", "after_orders"), policy
    events = np.asarray(event_times, dtype=np.int64)
    units = np.asarray(unit_fees, dtype=float)
    snapshot_times = np.asarray(clocks, dtype=np.int64)
    assert events.ndim == units.ndim == snapshot_times.ndim == 1
    assert len(events) == len(units) and np.isfinite(units).all()
    assert np.all(np.diff(events) > 0) and np.all(np.diff(snapshot_times) > 0)
    orders = trade["orders"]
    assert len(orders) == 2, "Funding contract forbids DCA/partial exits"
    entry, exit_order = orders
    assert entry["ft_is_entry"] and not exit_order["ft_is_entry"]
    quantity = float(entry["amount"])
    assert math.isfinite(quantity) and quantity > 0
    assert D(str(entry["amount"])) == D(str(exit_order["amount"]))
    opened = int(entry["order_filled_timestamp"])
    closed = int(exit_order["order_filled_timestamp"])
    assert opened <= closed
    assert opened == trade["open_timestamp"] and closed == trade["close_timestamp"]
    assert abs(float(trade["amount"]) - quantity) < 1e-10
    if policy == "before_orders":
        owned = (events > opened) & (events <= closed)
    else:
        owned = (events >= opened) & (events < closed)
    direction = 1 if trade["is_short"] else -1
    fees = np.where(owned, quantity * direction * units, 0.0)
    accrued = np.concatenate([[0.0], np.cumsum(fees)])[
        np.searchsorted(events, snapshot_times, side="left")
    ]
    boundary = [
        dict(
            timestamp_ms=int(event),
            entry_boundary=bool(event == opened),
            exit_boundary=bool(event == closed),
            owned_by_this_trade=bool(own),
            fee_usdt=float(fee),
        )
        for event, own, fee in zip(events, owned, fees, strict=True)
        if event == opened or event == closed
    ]
    return dict(
        fees=fees,
        accrued=accrued,
        ownership=owned,
        boundary=boundary,
        total_funding=math.fsum(fees.tolist()),
    )


def require_single_ownership(ownership):
    for pair, counts in ownership.items():
        assert np.all(counts <= 1), (
            "Funding event assigned to multiple lifecycles",
            pair,
            np.flatnonzero(counts > 1).tolist(),
        )


def audit(candidate, plan, grid, oracle):
    d = Path(plan["output_root"]) / (candidate["name"] + f"_{candidate['cost_bps']}bps")
    receipt = read(d / "process_receipt.json")
    assert receipt["returncode"] == 0 and sha(d / "result.json") == receipt["result_sha256"]
    stats, archive = native_stats(d, candidate["strategy"])
    config = read(d / "config.json")
    policy = candidate["funding_ownership"]
    assert policy in ("before_orders", "after_orders")
    assert candidate["mode"] == "futures"
    assert sha(candidate["config"]["path"]) == candidate["config"]["sha256"]
    assert sha(candidate["market_snapshot"]["path"]) == candidate["market_snapshot"]["sha256"]
    extension = plan["funding_extension"]
    assert sha(extension["path"]) == extension["sha256"]
    policy_metadata = read(d / "funding_policy.json")
    assert policy_metadata["mode"] == policy and policy_metadata["implementation"] == extension
    extension_audit = read(d / "funding_extension_audit.json")
    assert extension_audit["policy"] == policy and extension_audit["closing_context_empty"]
    assert not stats["trades"] or extension_audit["active_native_funding_calls"] > 0
    assert (
        config["dry_run_wallet"] == candidate["capital"]
        and config["fee"] == candidate["cost_bps"] / 10000
    )
    assert (
        config["max_open_trades"] == 2
        and config["exchange"]["pair_whitelist"] == candidate["pairs"]
    )
    assert config["trading_mode"] == candidate["mode"]
    assert config["margin_mode"] == ("isolated" if candidate["mode"] == "futures" else "")
    for name, source in candidate["source_files"].items():
        assert sha(source["path"]) == source["sha256"]
        assert sha(d / "strategies" / name) == source["sha256"]
    assert sha(plan["data_receipts"]["path"]) == plan["data_receipts"]["sha256"]
    data_receipts = read(plan["data_receipts"]["path"])
    frame = pd.read_parquet(d / "hourly_equity.parquet")
    assert pd.DatetimeIndex(frame.date).as_unit("ns").equals(grid)
    clocks = grid.as_unit("ms").asi8
    total = np.full(len(grid), float(candidate["capital"]))
    counts = np.zeros(len(grid), dtype=int)
    gross = np.zeros(len(grid))
    signed = gross.copy()
    cash_errors = []
    fund_errors = []
    fills = multi = 0
    boundary = []
    data = {}
    ownership = {}
    for pair in candidate["pairs"]:
        stem = pair.replace("/", "_").replace(":", "_")
        root = Path(plan["datadir"])
        if candidate["mode"] == "futures":
            root = root / "futures"
            for suffix in ("1h-mark", "1h-funding_rate"):
                name = stem + "-" + suffix + ".feather"
                assert sha(root / name) == data_receipts["futures/" + name]
            marks = (
                pd.read_feather(root / (stem + "-1h-mark.feather"))
                .set_index("date")
                .open.reindex(grid)
            )
            f = pd.read_feather(root / (stem + "-1h-funding_rate.feather"))
            f = f.loc[f.date.ge(pd.Timestamp(plan["start"])) & f.date.lt(pd.Timestamp(plan["end"]))]
            event_times = pd.DatetimeIndex(f.date).as_unit("ms").asi8
            assert len(f) == 1824 and np.array_equal(
                event_times,
                pd.date_range(plan["start"], plan["comparison_end"], freq="8h").as_unit("ms").asi8,
            )
            unit = f.funding_rate.to_numpy() * marks.reindex(pd.DatetimeIndex(f.date)).to_numpy()
        else:
            marks = (
                pd.read_feather(root / (stem + "-1h.feather")).set_index("date").open.reindex(grid)
            )
            event_times = np.array([], dtype=np.int64)
            unit = np.array([])
        assert np.isfinite(marks).all() and (marks > 0).all()
        assert np.isfinite(unit).all()
        data[pair] = dict(prices=marks.to_numpy(), times=event_times, unit=unit)
        ownership[pair] = np.zeros(len(event_times), dtype=int)
    for trade in stats["trades"]:
        assert trade["leverage"] == 1 and trade["pair"] in candidate["pairs"]
        assert (candidate["mode"] == "futures") or not trade["is_short"]
        assert trade["fee_open"] == trade["fee_close"] == config["fee"]
        if candidate["mode"] == "spot":
            assert not trade["funding_fees"]
        cash = oracle.cash_from_orders({**trade, "funding_fees": trade["funding_fees"] or 0})
        cash_errors.append(float(cash["absolute_error"]))
        assert abs(D(cash["quantity_residual"])) < D("1e-8")
        orders = trade["orders"]
        fills += len(orders)
        multi += len(orders) > 2
        times = np.array([o["order_filled_timestamp"] for o in orders], dtype=np.int64)
        assert np.all(times[1:] >= times[:-1])
        quantities = np.array(
            [o["amount"] if o["ft_is_entry"] else -o["amount"] for o in orders]
        ).cumsum()
        values = np.array([o["amount"] * o["safe_price"] for o in orders])
        bought = np.cumsum(
            [v if o["ft_is_entry"] else 0 for v, o in zip(values, orders, strict=True)]
        )
        sold = np.cumsum(
            [v if not o["ft_is_entry"] else 0 for v, o in zip(values, orders, strict=True)]
        )
        before = np.searchsorted(times, clocks, side="left")
        held = np.concatenate([[0.0], quantities])[before]
        held[np.abs(held) < 1e-10] = 0.0
        assert held.min() >= -1e-8 and abs(quantities[-1]) < 1e-8
        entry = np.concatenate([[0.0], bought])[before]
        exit = np.concatenate([[0.0], sold])[before]
        pair = data[trade["pair"]]
        ev = pair["times"]
        sign = -1 if trade["is_short"] else 1
        funding = funding_path(trade, ev, pair["unit"], clocks, policy)
        ownership[trade["pair"]] += funding["ownership"].astype(int)
        boundary.extend(
            dict(
                pair=trade["pair"],
                open_timestamp=trade["open_timestamp"],
                close_timestamp=trade["close_timestamp"],
                is_short=trade["is_short"],
                policy=policy,
                **event,
            )
            for event in funding["boundary"]
        )
        fund_errors.append(abs(funding["total_funding"] - (trade["funding_fees"] or 0)))
        accrued = funding["accrued"]
        total += (
            sign * (exit + held * pair["prices"] - entry)
            - entry * trade["fee_open"]
            - exit * trade["fee_close"]
            - held * pair["prices"] * trade["fee_close"]
            + accrued
        )
        counts += (held > 0).astype(int)
        gross += held * pair["prices"]
        signed += sign * held * pair["prices"]
        market = read(candidate["market_snapshot"]["path"])["markets"][trade["pair"]]
        for o in orders:
            for value, step in [
                (o["amount"], market["precision"]["amount"]),
                (o["safe_price"], market["precision"]["price"]),
            ]:
                normalized = D(str(value)) / D(str(step))
                assert abs(normalized - normalized.to_integral_value()) < D("1e-8")
    require_single_ownership(ownership)
    error = float(np.max(np.abs(total - frame.hourly_mark_equity.to_numpy())))
    assert error < 3e-5, error
    assert np.array_equal(counts, frame.open_trades.to_numpy())
    assert max(cash_errors, default=0) < 1e-5 and max(fund_errors, default=0) < 1e-7
    assert abs(stats["final_balance"] - candidate["capital"] - stats["profit_total_abs"]) < 1e-6
    nav = frame.set_index("date").hourly_mark_equity
    computed = periods(nav, plan)
    reported = read(d / "result.json")["periods"]
    for a, b in zip(computed, reported, strict=True):
        for key in ["net_return", "hourly_mark_drawdown", "daily_nav_sharpe"]:
            if a[key] is None or b[key] is None:
                assert a[key] is b[key]
            else:
                assert abs(a[key] - b[key]) < 1e-12
        assert a["complete_24h_returns"] == b["complete_24h_returns"]
    assert (
        abs(
            (1 + computed[1]["net_return"]) * (1 + computed[2]["net_return"])
            - 1
            - computed[0]["net_return"]
        )
        < 1e-12
    )
    audit_result = dict(
        passed=True,
        archive_sha256=sha(archive),
        actual_native_trades=len(stats["trades"]),
        filled_orders=fills,
        multi_order_trades=multi,
        hour_points=len(grid),
        maximum_nav_error_usdt=error,
        maximum_cash_error_usdt=max(cash_errors, default=0),
        maximum_funding_error_usdt=max(fund_errors, default=0),
        funding_ownership=policy,
        funding_boundary_fills=boundary,
        funding_input_events=sum(len(x) for x in ownership.values()),
        assigned_event_lifecycles=sum(int(x.sum()) for x in ownership.values()),
        dual_owned_events=sum(int((x > 1).sum()) for x in ownership.values()),
        funding_policy_metadata_sha256=sha(d / "funding_policy.json"),
        funding_extension_audit_sha256=sha(d / "funding_extension_audit.json"),
        maximum_gross_exposure_to_nav=float(np.max(gross / nav.to_numpy())),
        maximum_abs_net_exposure_to_nav=float(np.max(np.abs(signed) / nav.to_numpy())),
        native_funding_usdt=math.fsum(t["funding_fees"] or 0 for t in stats["trades"]),
        naturally_closed_before_endpoint=int(
            sum(
                t["exit_reason"] != "force_exit" and t["close_timestamp"] < clocks[-1]
                for t in stats["trades"]
            )
        ),
        open_trades_at_common_endpoint=int(counts[-1]),
        hypothetical_terminal_force_exits=sum(
            t["exit_reason"] == "force_exit" for t in stats["trades"]
        ),
        private_settlement_certified=False,
    )
    save(d / "independent_account_audit.json", audit_result)
    return nav, dict(
        account=candidate["name"],
        role=candidate["name"].split("_")[0],
        capital=candidate["capital"],
        scenario=candidate["scenario"],
        periods=computed,
        **audit_result,
    )
