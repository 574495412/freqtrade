"""Read-only exported-fill/price-calendar diagnostic; no native/account simulation."""

import ast
import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
HOUR, FIVE = 3_600_000, 300_000
D = Decimal


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(name, value):
    with (HERE / name).open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def iso(ms):
    return pd.Timestamp(ms, unit="ms", tz="UTC").isoformat()


def ms(value):
    return int(pd.Timestamp(value).value // 1_000_000)


def campaign_cash(trade):
    entry = sum(
        (
            D(str(o["amount"])) * D(str(o["safe_price"]))
            for o in trade["orders"]
            if o["ft_is_entry"]
        ),
        D(0),
    )
    exit_value = sum(
        (
            D(str(o["amount"])) * D(str(o["safe_price"]))
            for o in trade["orders"]
            if not o["ft_is_entry"]
        ),
        D(0),
    )
    q = sum((D(str(o["amount"])) * (1 if o["ft_is_entry"] else -1) for o in trade["orders"]), D(0))
    gross = (exit_value - entry) * (-1 if trade["is_short"] else 1)
    fees = entry * D(str(trade["fee_open"])) + exit_value * D(str(trade["fee_close"]))
    funding = D(str(trade["funding_fees"]))
    pnl = gross - fees + funding
    return {
        "entry_notional": float(entry),
        "exit_notional": float(exit_value),
        "gross_price_pnl": float(gross),
        "fees_paid": float(fees),
        "funding_native": float(funding),
        "reconstructed_net": float(pnl),
        "native_net": trade["profit_abs"],
        "native_cash_error": float(pnl - D(str(trade["profit_abs"]))),
        "quantity_residual": str(q),
    }


def load_native(record):
    directory = Path(record["directory"])
    archives = list((directory / "native_results").glob("*.zip"))
    assert len(archives) == 1
    with ZipFile(archives[0]) as archive:
        assert archive.testzip() is None
        payloads = [
            read_json(archive.read(n))
            for n in archive.namelist()
            if n.endswith(".json") and not n.endswith("_config.json")
        ]
        stats = [p["strategy"]["NostalgiaForInfinityX8"] for p in payloads if "strategy" in p]
        assert len(stats) == 1
        configs = [
            read_json(archive.read(n)) for n in archive.namelist() if n.endswith("_config.json")
        ]
        sources = [n for n in archive.namelist() if n.endswith("NostalgiaForInfinityX8.py")]
        assert len(configs) == len(sources) == 1
        source_sha = hashlib.sha256(archive.read(sources[0])).hexdigest()
    frame = pd.read_parquet(directory / "hourly_equity.parquet").set_index("date")
    frame.index = pd.DatetimeIndex(frame.index).as_unit("ms")
    result = read(directory / "result.json")
    if record["capital"] == 500:
        process = read(directory / "process_receipt.json")
        assert process["returncode"] == 0 and process["result_sha256"] == sha(
            directory / "result.json"
        )
    return stats[0], configs[0], frame, result, archives[0], source_sha


def read_json(value):
    return json.loads(value)


def run():
    protocol = read(HERE / "protocol.json")
    frozen = read(HERE / "diagnostic_freeze_v2.json")
    for path, digest in frozen["files"].items():
        assert sha(path) == digest, path
    inputs = dict(frozen["files"])
    source_audit = Path(protocol["funding_diagnostic_source"])
    tree = ast.parse(source_audit.read_text())
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "funding_history"
    ]
    assert len(functions) == 1
    scope = {"np": np, "math": math, "Decimal": Decimal, "HOUR": HOUR}
    exec(
        compile(
            ast.Module(body=functions, type_ignores=[]),
            str(source_audit),
            "exec",
            dont_inherit=True,
        ),
        scope,
    )
    funding_history = scope["funding_history"]
    accounts = {}
    fills = []
    campaigns = []
    selected_clocks = {ms(t) for t in protocol["fixed_snapshot_times"]}
    pairs = None
    for record in protocol["accounts"]:
        name = record["name"]
        stats, config, curve, result, archive, source_sha = load_native(record)
        assert source_sha == protocol["source_sha256"]
        assert config["dry_run_wallet"] == record["capital"] and config["fee"] == 0.0015
        assert config["timeframe"] == "5m" and not config.get("timeframe_detail")
        assert (
            config["max_open_trades"] == 6
            and stats["pairlist"] == config["exchange"]["pair_whitelist"]
        )
        assert all(t["leverage"] == 1 for t in stats["trades"])
        assert curve.index.equals(
            pd.date_range(protocol["start"], protocol["end"], freq="1h", inclusive="left").as_unit(
                "ms"
            )
        )
        if pairs is None:
            pairs = stats["pairlist"]
        assert pairs == stats["pairlist"]
        changes = curve.hourly_mark_equity.pct_change(fill_method=None).dropna()
        chosen = list(changes.nlargest(5).index) + list(changes.nsmallest(3).index)
        for time in chosen:
            selected_clocks.update([ms(time), ms(time) - HOUR])
        accounts[name] = {
            "record": record,
            "stats": stats,
            "curve": curve,
            "top_times": chosen,
            "maximum_jump": ms(changes.idxmax()),
            "source_sha256": source_sha,
        }
        for path in [
            archive,
            Path(record["directory"]) / "hourly_equity.parquet",
            Path(record["directory"]) / "result.json",
        ]:
            inputs[str(path)] = sha(path)
        for i, trade in enumerate(stats["trades"]):
            cash = campaign_cash(trade)
            campaigns.append(
                {
                    "account": name,
                    "trade_index": i,
                    "pair": trade["pair"],
                    "open": iso(trade["open_timestamp"]),
                    "close": iso(trade["close_timestamp"]),
                    "is_short": trade["is_short"],
                    "exit_reason": trade["exit_reason"],
                    "entry_tag": trade["enter_tag"],
                    "order_count": len(trade["orders"]),
                    **cash,
                }
            )
            for j, order in enumerate(trade["orders"]):
                when = order["order_filled_timestamp"]
                fills.append(
                    {
                        "account": name,
                        "trade_index": i,
                        "order_index": j,
                        "pair": trade["pair"],
                        "time_ms": when,
                        "date": iso(when),
                        "is_entry": order["ft_is_entry"],
                        "is_short": trade["is_short"],
                        "amount": order["amount"],
                        "price": order["safe_price"],
                        "native_cost": order["cost"],
                        "tag": order.get("ft_order_tag"),
                        "off_5m_clock": when % FIVE != 0,
                    }
                )
    clocks = np.asarray(sorted(selected_clocks), dtype=np.int64)
    prices, funding = {}, {}
    calendar = []
    price_window, mark_window = [], []
    expected = pd.date_range(
        protocol["start"], protocol["end"], freq="5min", inclusive="left"
    ).as_unit("ms")
    left, right = ms(protocol["detail_start"]), ms(protocol["detail_end"])
    for pair in pairs:
        stem = pair.replace("/", "_").replace(":", "_")
        base = Path(protocol["datadir"]) / "futures"
        path = base / (stem + "-5m-futures.feather")
        inputs[str(path)] = sha(path)
        frame = pd.read_feather(path).set_index("date")
        frame.index = pd.DatetimeIndex(frame.index).as_unit("ms")
        dates = frame.index
        window = frame.loc[
            pd.Timestamp(left, unit="ms", tz="UTC") : pd.Timestamp(
                right - FIVE, unit="ms", tz="UTC"
            )
        ]
        missing = expected.difference(dates)
        invalid = (
            (frame.high < frame[["open", "close", "low"]].max(axis=1))
            | (frame.low > frame[["open", "close", "high"]].min(axis=1))
            | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
            | frame.volume.lt(0)
            | ~np.isfinite(frame[["open", "high", "low", "close", "volume"]]).all(axis=1)
        )
        calendar.append(
            {
                "pair": pair,
                "fullwindow_expected_rows": len(expected),
                "missing_fullwindow_5m": len(missing),
                "duplicate_5m": int(dates.duplicated().sum()),
                "nonmonotonic": not dates.is_monotonic_increasing,
                "off_5m_calendar": int((dates.asi8 % FIVE != 0).sum()),
                "invalid_ohlcv_rows": int(invalid.sum()),
                "detail_rows": len(window),
                "detail_expected_rows": (right - left) // FIVE,
                "zero_volume_detail_rows": int(window.volume.eq(0).sum()),
                "missing_dates": [t.isoformat() for t in missing[:20]],
            }
        )
        for t, row in window.iterrows():
            price_window.append(
                {
                    "pair": pair,
                    "date": t.isoformat(),
                    **{key: float(row[key]) for key in ["open", "high", "low", "close", "volume"]},
                }
            )
        for fill in (f for f in fills if f["pair"] == pair):
            t = pd.Timestamp(fill["time_ms"], unit="ms", tz="UTC")
            if t not in dates:
                fill["price_calendar_status"] = "MISSING_BAR"
                continue
            row = frame.loc[t]
            for key in ("open", "high", "low", "close", "volume"):
                fill["bar_" + key] = float(row[key])
            price = D(str(fill["price"]))
            fill["within_bar_range"] = D(str(row.low)) <= price <= D(str(row.high))
            fill["price_equal_bar_open"] = price == D(str(row.open))
            fill["price_minus_open"] = float(price - D(str(row.open)))
            fill["price_minus_open_bps"] = float((price / D(str(row.open)) - 1) * 10000)
            fill["price_calendar_status"] = "PRESENT"
        del frame, window
        path = base / (stem + "-1h-mark.feather")
        inputs[str(path)] = sha(path)
        marks = pd.read_feather(path).set_index("date").open
        marks.index = pd.DatetimeIndex(marks.index).as_unit("ms")
        points = marks.reindex(pd.to_datetime(clocks, unit="ms", utc=True))
        assert np.isfinite(points).all()
        prices[pair] = points.to_numpy()
        for t, value in marks.loc[
            pd.Timestamp(left, unit="ms", tz="UTC") : pd.Timestamp(right, unit="ms", tz="UTC")
        ].items():
            mark_window.append({"pair": pair, "date": t.isoformat(), "mark_open": float(value)})
        path = base / (stem + "-1h-funding_rate.feather")
        inputs[str(path)] = sha(path)
        rates = pd.read_feather(path)
        events = pd.DatetimeIndex(rates.date).as_unit("ms").asi8
        units = (
            rates.funding_rate.to_numpy()
            * marks.reindex(pd.DatetimeIndex(rates.date).as_unit("ms")).to_numpy()
        )
        assert np.isfinite(units).all() and np.all(np.diff(events) > 0)
        funding[pair] = events, units

    hour_details, snapshots = [], []
    results = {}
    for name, account in accounts.items():
        stats, curve = account["stats"], account["curve"]
        all_states = []
        funding_errors, boundary_rows = [], []
        for i, trade in enumerate(stats["trades"]):
            orders = trade["orders"]
            times = np.asarray([o["order_filled_timestamp"] for o in orders], dtype=np.int64)
            assert np.all(np.diff(times) >= 0)
            q = entry = exit_value = D(0)
            quantities, bought, sold = [0.0], [0.0], [0.0]
            for order in orders:
                amount = D(str(order["amount"]))
                value = amount * D(str(order["safe_price"]))
                if order["ft_is_entry"]:
                    q += amount
                    entry += value
                else:
                    q -= amount
                    exit_value += value
                quantities.append(float(q))
                bought.append(float(entry))
                sold.append(float(exit_value))
            before = np.searchsorted(times, clocks, side="left")
            held = np.asarray(quantities)[before]
            held[clocks > trade["close_timestamp"]] = 0.0
            entry = np.asarray(bought)[before]
            exit_value = np.asarray(sold)[before]
            events, units = funding[trade["pair"]]
            accrued, terminal, boundaries = funding_history(trade, events, units, clocks)
            funding_errors.append(abs(terminal - trade["funding_fees"]))
            boundary_rows.extend({"trade_index": i, "pair": trade["pair"], **b} for b in boundaries)
            direction = -1 if trade["is_short"] else 1
            mark = prices[trade["pair"]]
            gross = direction * (exit_value + held * mark - entry)
            paid = entry * trade["fee_open"] + exit_value * trade["fee_close"]
            reserve = held * mark * trade["fee_close"]
            net = gross - paid - reserve + accrued
            all_states.append(
                {
                    "gross": gross,
                    "paid": paid,
                    "reserve": reserve,
                    "funding": accrued,
                    "net": net,
                    "held": held,
                }
            )
        nav = account["record"]["capital"] + np.sum([s["net"] for s in all_states], axis=0)
        selected_curve = curve.reindex(pd.to_datetime(clocks, unit="ms", utc=True))
        valid = selected_curve.hourly_mark_equity.notna().to_numpy()
        errors = np.abs(nav[valid] - selected_curve.hourly_mark_equity.to_numpy()[valid])
        for k, t in enumerate(clocks):
            if not valid[k]:
                continue
            snapshots.append(
                {
                    "account": name,
                    "date": iso(t),
                    "reconstructed_marked_nav": float(nav[k]),
                    "saved_marked_nav": float(selected_curve.hourly_mark_equity.iloc[k]),
                    "difference": float(nav[k] - selected_curve.hourly_mark_equity.iloc[k]),
                    "gross_price_pnl_cumulative": float(sum(s["gross"][k] for s in all_states)),
                    "fees_paid_cumulative": float(sum(s["paid"][k] for s in all_states)),
                    "remaining_exit_fee_reserve": float(sum(s["reserve"][k] for s in all_states)),
                    "funding_native_model_accrued": float(sum(s["funding"][k] for s in all_states)),
                }
            )
        chosen_times = sorted(
            {ms(t) for t in account["top_times"]}
            | {ms("2025-02-03T04:00Z"), ms("2025-02-03T06:00Z"), account["maximum_jump"]}
        )
        summaries = []
        for t in chosen_times:
            a, b = int(np.searchsorted(clocks, t - HOUR)), int(np.searchsorted(clocks, t))
            assert clocks[a] == t - HOUR and clocks[b] == t
            before_row, after_row = (
                curve.loc[pd.Timestamp(t - HOUR, unit="ms", tz="UTC")],
                curve.loc[pd.Timestamp(t, unit="ms", tz="UTC")],
            )
            detail = []
            for i, (trade, state) in enumerate(zip(stats["trades"], all_states, strict=True)):
                delta = float(state["net"][b] - state["net"][a])
                if abs(delta) < 1e-9 and not (state["held"][a] or state["held"][b]):
                    continue
                row = {
                    "account": name,
                    "hour_end": iso(t),
                    "trade_index": i,
                    "pair": trade["pair"],
                    "is_short": trade["is_short"],
                    "open": iso(trade["open_timestamp"]),
                    "close": iso(trade["close_timestamp"]),
                    "quantity_before": float(state["held"][a]),
                    "quantity_after": float(state["held"][b]),
                    "gross_price_pnl_delta": float(state["gross"][b] - state["gross"][a]),
                    "fees_paid_delta": float(state["paid"][b] - state["paid"][a]),
                    "exit_reserve_delta": float(state["reserve"][b] - state["reserve"][a]),
                    "funding_accrued_delta": float(state["funding"][b] - state["funding"][a]),
                    "net_nav_contribution": delta,
                    "prior_marked_contribution": float(state["net"][a]),
                    "terminal_native_profit": trade["profit_abs"],
                    "exit_reason": trade["exit_reason"],
                    "fills_in_hour": sum(
                        t - HOUR <= o["order_filled_timestamp"] < t for o in trade["orders"]
                    ),
                }
                detail.append(row)
                hour_details.append(row)
            jump = float(after_row.hourly_mark_equity - before_row.hourly_mark_equity)
            summaries.append(
                {
                    "hour_start": iso(t - HOUR),
                    "hour_end": iso(t),
                    "marked_nav_before": float(before_row.hourly_mark_equity),
                    "marked_nav_after": float(after_row.hourly_mark_equity),
                    "jump_usdt": jump,
                    "jump_ratio": float(
                        after_row.hourly_mark_equity / before_row.hourly_mark_equity - 1
                    ),
                    "campaign_contribution_sum": sum(row["net_nav_contribution"] for row in detail),
                    "contribution_residual": sum(row["net_nav_contribution"] for row in detail)
                    - jump,
                    "closed_realized_delta": float(
                        after_row.closed_realized_pnl - before_row.closed_realized_pnl
                    ),
                    "mark_open_contribution_before": float(
                        before_row.hourly_mark_equity
                        - account["record"]["capital"]
                        - before_row.closed_realized_pnl
                    ),
                    "mark_open_contribution_after": float(
                        after_row.hourly_mark_equity
                        - account["record"]["capital"]
                        - after_row.closed_realized_pnl
                    ),
                    "open_trades_before": int(before_row.open_trades),
                    "open_trades_after": int(after_row.open_trades),
                    "gross_price_pnl_delta": sum(row["gross_price_pnl_delta"] for row in detail),
                    "fees_paid_delta": sum(row["fees_paid_delta"] for row in detail),
                    "exit_reserve_delta": sum(row["exit_reserve_delta"] for row in detail),
                    "funding_accrued_delta": sum(row["funding_accrued_delta"] for row in detail),
                    "affected_campaigns": len(detail),
                }
            )
        account_fills = [f for f in fills if f["account"] == name]
        frame = pd.DataFrame(account_fills)
        campaign_both = frame.groupby(["trade_index", "time_ms"]).is_entry.nunique()
        pair_both = frame.groupby(["pair", "time_ms"]).is_entry.nunique()
        account_both = frame.groupby("time_ms").is_entry.nunique()
        same_cycles = [
            i
            for i, t in enumerate(stats["trades"])
            if len({o["order_filled_timestamp"] // FIVE for o in t["orders"]}) == 1
        ]
        own_campaigns = [c for c in campaigns if c["account"] == name]
        largest = max(summaries, key=lambda r: r["jump_ratio"])
        results[name] = {
            "capital": account["record"]["capital"],
            "campaigns": len(stats["trades"]),
            "fills": len(account_fills),
            "same_campaign_entry_exit_5m_groups": int(campaign_both.eq(2).sum()),
            "same_pair_entry_exit_5m_groups": int(pair_both.eq(2).sum()),
            "account_5m_clocks_with_both_entry_exit_different_campaigns": int(
                account_both.eq(2).sum()
            ),
            "complete_same_5m_cycles": len(same_cycles),
            "complete_same_5m_cycles_native_net_profit": sum(
                stats["trades"][i]["profit_abs"] for i in same_cycles
            ),
            "entries_below_bar_open": sum(
                f["is_entry"] and f.get("price_minus_open", 0) < 0 for f in account_fills
            ),
            "entries_above_bar_open": sum(
                f["is_entry"] and f.get("price_minus_open", 0) > 0 for f in account_fills
            ),
            "entries_exactly_bar_open": sum(
                f["is_entry"] and f.get("price_equal_bar_open", False) for f in account_fills
            ),
            "fills_outside_samebar_range": sum(
                not f.get("within_bar_range", False) for f in account_fills
            ),
            "fill_calendar_missing": sum(
                f["price_calendar_status"] != "PRESENT" for f in account_fills
            ),
            "off_5m_fill_timestamps": sum(f["off_5m_clock"] for f in account_fills),
            "maximum_campaign_cash_error": max(abs(c["native_cash_error"]) for c in own_campaigns),
            "maximum_funding_reconstruction_error": max(funding_errors),
            "funding_boundary_fill_count": len(boundary_rows),
            "funding_boundary_scope": "Conditional source-model reconstruction; minified order creation missing remains UNKNOWN for deferred/DCA boundary paths",
            "selected_hourly_snapshots": int(valid.sum()),
            "maximum_selected_NAV_error": float(max(errors)),
            "maximum_jump": largest,
            "selected_hours": summaries,
            "hourly_mark_drawdown_only": float(
                (1 - curve.hourly_mark_equity / curve.hourly_mark_equity.cummax()).max()
            ),
            "intrahour_worst_case_or_real_execution_drawdown": "UNKNOWN",
            "native_terminal_profit": stats["profit_total_abs"],
            "all_campaign_cash_sum": sum(c["reconstructed_net"] for c in own_campaigns),
        }
    for filename, rows in [
        ("campaign_cash.csv", campaigns),
        ("all_native_fills_with_bars.csv", fills),
        ("hourly_campaign_contributions.csv", hour_details),
        ("selected_snapshot_reconciliation.csv", snapshots),
        ("oct10_5m_prices.csv", price_window),
        ("oct10_hourly_marks.csv", mark_window),
    ]:
        pd.DataFrame(rows).to_csv(HERE / filename, index=False)
    detail_fills = [f for f in fills if left <= f["time_ms"] < right]
    pd.DataFrame(detail_fills).to_csv(HERE / "oct10_native_fills.csv", index=False)
    save("input_hashes.json", inputs)
    save(
        "calendar_audit.json",
        {
            "pairs": calendar,
            "total_missing_5m": sum(r["missing_fullwindow_5m"] for r in calendar),
            "total_invalid_ohlcv": sum(r["invalid_ohlcv_rows"] for r in calendar),
        },
    )
    save(
        "results.json",
        {
            "scope": "Exported native fills, frozen OHLC/mark calendars and saved-NAV reconciliation only",
            "accounts": results,
            "new_native_accounts_started": 0,
            "real_exchange_fill_or_tick_path_certified": False,
            "ohlc_real_rebound_claim": "Frozen recorded prices show successive-bar rebound; no independent exchange-tick authentication performed",
            "hypothesized_samebar_cycle_explanation": "Evaluate actual export counts; permitted matcher behavior alone is not evidence it occurred",
        },
    )
    print(
        json.dumps(
            {
                name: {
                    key: record[key]
                    for key in [
                        "campaigns",
                        "fills",
                        "complete_same_5m_cycles",
                        "fills_outside_samebar_range",
                        "maximum_selected_NAV_error",
                        "maximum_jump",
                    ]
                }
                for name, record in results.items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    run()
