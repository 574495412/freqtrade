"""Independent read-only audit; never imports or starts a native account engine.

metrics/funding_history are AST-preserved from the frozen NFI500 independent
reconciler. Their numeric replay is supporting evidence: missing boundary order
metadata remains UNKNOWN even when every aggregate matches.
"""

import argparse
import gzip
import hashlib
import json
import math
from decimal import Decimal, localcontext
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

from research.crypto.head_shoulders_audit_evidence_v3_20261118_v1.evidence_bridge import (
    load_conditional_scope, supplement_boundary_evidence, verify_scope_coverage, account_evidence_fields,
)


if not __debug__:
    raise RuntimeError("Audit assertions require ordinary Python, without -O")

HERE = Path(__file__).absolute().parent
BASE_STUDY = HERE.parent / "head_shoulders_financial_native_20261117_v3"
HOUR = 3600000
MISSING_SLOT = 1782273600000
RISK_EXITS = {"stop_loss", "liquidation", "force_exit"}
SOURCE_EXITS = {"hs_source_target", "hs_source_stop", "hs_source_expiry"}


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False, default=str) + "\n")


def ms(value):
    return int(pd.Timestamp(value).timestamp() * 1000)


def verified(record):
    path = Path(record["path"])
    assert sha(path) == record["sha256"], str(path)
    return path


def metrics(nav):
    assert np.isfinite(nav).all() and (nav > 0).all()
    daily = nav.reindex(pd.date_range(nav.index[0], nav.index[-1], freq="24h"))
    changes = daily.pct_change(fill_method=None).dropna()
    deviation = changes.std(ddof=1)
    years = (nav.index[-1] - nav.index[0]).total_seconds() / (365 * 86400)
    return {
        "net_return": float(nav.iloc[-1] / nav.iloc[0] - 1),
        "initial_marked_equity": float(nav.iloc[0]),
        "ending_marked_equity": float(nav.iloc[-1]),
        "hourly_mark_drawdown": float((1 - nav / nav.cummax()).max()),
        "cagr_365day": float((nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1),
        "complete_24h_daily_nav_sharpe": float(changes.mean() / deviation * math.sqrt(365))
        if deviation > 0
        else None,
        "complete_24h_returns": len(changes),
        "calendar_hours": len(nav),
    }


def funding_history(trade, events, units, clocks):
    """Cash audit only: hourly refresh, then immediate source fills in array order.

    At a fill, native close_bt_order transfers the previous running amount to
    the order. Entry recalc and exit recalc then differ from a venue cashflow
    convention. Boundary DCA is retained and explicitly inventoried; equality
    with the final native funding and every saved NAV is required for admission.
    Minified exports lack order creation timestamps, so deferred boundary entry
    ordering is not certified independently of those aggregate cross-checks.
    """
    orders = trade["orders"]
    first, last = orders[0]["order_filled_timestamp"], orders[-1]["order_filled_timestamp"]
    grouped = {}
    for order in orders:
        grouped.setdefault(order["order_filled_timestamp"], []).append(order)
    hours = range(((first + HOUR - 1) // HOUR) * HOUR, last + 1, HOUR)
    times = sorted(set(hours) | set(grouped))
    quantity = Decimal(0)
    stored = running = total = 0.0
    last_fill = first
    history_times, history_values, boundary = [], [], []
    sign = 1 if trade["is_short"] else -1
    count = 0

    def fees(start, end, amount):
        lo, hi = np.searchsorted(events, [start, end], side="left")
        if hi < len(events) and events[hi] == end:
            hi += 1
        return sign * math.fsum((units[lo:hi] * float(amount)).tolist())

    for when in times:
        if quantity > 0 and when % HOUR == 0:
            running = fees(last_fill, when, quantity)
            total = stored + running
        for order in grouped.get(when, []):
            if when in events:
                boundary.append({"time_ms": when, "is_entry": order["ft_is_entry"], "index": count})
            initial = count == 0
            stored += running
            running = 0.0
            amount = Decimal(str(order["amount"]))
            if order["ft_is_entry"]:
                quantity += amount
                running = fees(when, when, quantity)
                # Native _enter_trade's final recalc drops the running component
                # from funding_fees, without clearing funding_fee_running.
                total = stored
                if initial and when % HOUR == 0:
                    # Initial entry is followed by _check_trade_exit that hour.
                    total = stored + running
            else:
                running = fees(when, when, quantity)
                quantity -= amount
                total = stored
            last_fill = when
            count += 1
        history_times.append(when)
        history_values.append(total)
    before = np.searchsorted(history_times, clocks, side="left")
    accrued = np.asarray([0.0, *history_values])[before]
    return accrued, total, boundary


def cash_from_orders(trade):
    """Decimal fill cash, including both commissions and native model funding."""
    with localcontext() as context:
        context.prec = 50

        def d(value):
            return Decimal(str(value))

        q = entry = exit_value = Decimal(0)
        for order in trade["orders"]:
            amount, price = d(order["amount"]), d(order["safe_price"])
            assert amount.is_finite() and amount > 0 and price.is_finite() and price > 0
            if order["ft_is_entry"]:
                q += amount
                entry += amount * price
            else:
                q -= amount
                exit_value += amount * price
        fees = entry * d(trade["fee_open"]) + exit_value * d(trade["fee_close"])
        pnl = (-1 if trade["is_short"] else 1) * (exit_value - entry)
        net = pnl - fees + d(trade["funding_fees"])
        return {
            "quantity_residual": str(q),
            "entry_cash": str(entry),
            "exit_cash": str(exit_value),
            "commission": str(fees),
            "price_pnl": str(pnl),
            "funding": str(trade["funding_fees"]),
            "net_pnl": str(net),
            "error": str(abs(net - d(trade["profit_abs"]))),
        }


def boundary_evidence(trade, event_times, strict_hs_proven=False):
    """No synthetic creation timestamps. Minified ZIP ambiguity is retained."""
    rows = []
    for index, order in enumerate(trade["orders"]):
        filled = int(order["order_filled_timestamp"])
        if filled not in event_times:
            continue
        created = order.get("order_timestamp")
        # Full export schema only: creation and fill clocks must both be present.
        # Event binding alone cannot identify every pending-order funding refresh.
        known = strict_hs_proven or (created is not None and int(created) == filled)
        rows.append(
            {
                "order_index": index,
                "filled_ms": filled,
                "created_ms": created,
                "is_entry": order["ft_is_entry"],
                "status": (
                    "STRICT_HS_NATIVE_SEQUENCE_PROVEN"
                    if strict_hs_proven
                    else "IMMEDIATE_FILL_CLOCKS_PRESENT"
                )
                if known
                else "UNKNOWN",
                "reason": None if known else "Missing or deferred order creation clock",
            }
        )
    return {
        "status": "UNKNOWN" if any(r["status"] == "UNKNOWN" for r in rows) else "VERIFIED",
        "boundary_fills": rows,
    }


def two_fill_funding(trade, events, units, clocks):
    """Closed-form oracle only after strict two-filled-order H&S proof succeeds."""
    entry, exit_time = trade["open_timestamp"], trade["close_timestamp"]
    quantity = float(trade["orders"][0]["amount"])
    sign = 1 if trade["is_short"] else -1
    amounts = np.where((events >= entry) & (events <= exit_time), units * quantity * sign, 0.0)
    cumulative = np.r_[0.0, np.cumsum(amounts)]
    before = np.searchsorted(events, clocks, side="left")
    # Force-close may have the final base-row timestamp; observers stop before it.
    return cumulative[before], math.fsum(amounts.tolist())


def missing_exposure(trades, pair, slot=MISSING_SLOT):
    """Before/after each same-clock fill, gross per campaign; never net sleeves."""
    rows = []
    for index, trade in enumerate(trades):
        if trade["pair"] != pair:
            continue
        if not trade["open_timestamp"] <= slot <= trade["close_timestamp"]:
            continue
        quantity = Decimal(0)
        states = []
        for order in trade["orders"]:
            when = int(order["order_filled_timestamp"])
            amount = Decimal(str(order["amount"])) * (1 if order["ft_is_entry"] else -1)
            if when < slot:
                quantity += amount
            elif when == slot:
                if not states:
                    states.append(quantity)
                quantity += amount
                states.append(quantity)
        if not states:
            states = [quantity]
        gross = max(abs(q) for q in states)
        rows.append(
            {
                "trade_index": index,
                "is_short": trade["is_short"],
                "states_quantity": [str(q) for q in states],
                "maximum_gross": str(gross),
            }
        )
    exposed = any(Decimal(r["maximum_gross"]) > 0 for r in rows)
    return {
        "pair": pair,
        "slot_ms": slot,
        "campaigns": rows,
        "status": "UNKNOWN" if exposed else "NO_GROSS_EXPOSURE",
        "rate_imputed": False,
    }


def source_terminal(event, frame, asof_ms):
    """Independent close-only equation, bounded to closes available by asof."""
    origin = pd.Timestamp("2023-08-01T00:00:00Z")
    confirmation = origin + pd.Timedelta(hours=int(event["break_i"]))
    expiry = origin + pd.Timedelta(hours=int(event["break_i"]) + int(event["head_width"]) - 1)
    assert int(event["head_width"]) >= 1
    assert ms(event["confirmation_bar_open"]) == ms(confirmation)
    assert ms(event["expiry_bar_open"]) == ms(expiry)
    stop = float(event["r_shoulder_p"])
    target = float(event["neck_end"]) + (1 if event["inverted"] else -1) * float(
        event["head_height"]
    )
    assert stop == event["source_stop_log_price"] and target == event["source_target_log_price"]
    available = pd.Timestamp(asof_ms, unit="ms", tz="UTC").floor("h") - pd.Timedelta(hours=1)
    end = min(expiry, available)
    if end < confirmation:
        return None
    rows = frame.loc[(frame.date >= confirmation) & (frame.date <= end)]
    expected = pd.date_range(confirmation, end, freq="h").as_unit("ns")
    assert pd.DatetimeIndex(rows.date).as_unit("ns").equals(expected), "Missing closed source hour"
    for row in rows.itertuples():
        price = float(np.log(row.close))
        assert np.isfinite(price)
        target_hit = price > target if event["inverted"] else price < target
        stop_hit = price < stop if event["inverted"] else price > stop
        reason = "hs_source_target" if target_hit else "hs_source_stop" if stop_hit else None
        if reason is None and row.date == expiry:
            reason = "hs_source_expiry"
        if reason:
            return {
                "reason": reason,
                "source_bar_open_ms": ms(row.date),
                "available_ms": ms(row.date) + HOUR,
                "close_log_price": price,
                "self_terminal": row.date == confirmation,
            }
    return None


def source_trade_check(trade, event, frame, force_clock=None):
    """Actual source exits must equal the first eligible terminal event clock."""
    assert trade["pair"] == event["pair"] and trade["enter_tag"] == event["event_id"]
    assert bool(trade["is_short"]) != bool(event["inverted"])
    assert not event["self_terminal_at_confirmation"]
    assert trade["open_timestamp"] == ms(event["entry_available"])
    assert ms(event["entry_available"]) == ms(event["confirmation_bar_open"]) + HOUR
    assert not trade["is_open"]
    # BT_DATA_COLUMNS omits the following fields. Validate them if a full export
    # supplies them; never fabricate their presence in the native minified ZIP.
    if "has_open_orders" in trade:
        assert not trade["has_open_orders"]
    for key in ("nr_of_successful_entries", "nr_of_successful_exits"):
        if key in trade:
            assert trade[key] == 1
    if "open_fill_timestamp" in trade:
        assert trade["open_fill_timestamp"] == trade["open_timestamp"]
    assert len(trade["orders"]) == 2 and trade["orders"][0]["ft_is_entry"]
    assert Decimal(str(trade["orders"][0]["amount"])) == Decimal(str(trade["orders"][1]["amount"]))
    assert not trade["orders"][1]["ft_is_entry"]
    assert trade["orders"][0]["order_filled_timestamp"] == trade["open_timestamp"]
    assert trade["orders"][1]["order_filled_timestamp"] == trade["close_timestamp"]
    assert trade["close_timestamp"] % 300000 == 0
    at_entry = source_terminal(event, frame, trade["open_timestamp"])
    assert at_entry is None, "Self-terminal or zero-duration pattern cannot be executed"
    terminal = source_terminal(event, frame, trade["close_timestamp"])
    reason = trade["exit_reason"]
    assert reason in SOURCE_EXITS | RISK_EXITS
    if reason in SOURCE_EXITS:
        assert terminal is not None and terminal["reason"] == reason
        assert terminal["available_ms"] == trade["close_timestamp"], "Late or premature source exit"
        category = "SOURCE_CLOSE_RULE"
    else:
        # Native CUSTOM_EXIT precedes stoploss/liquidation in the pinned engine.
        assert terminal is None, "Visible source exit has priority over native risk"
        if reason == "force_exit":
            assert force_clock is not None and trade["close_timestamp"] == force_clock
        category = "NATIVE_TERMINAL_FORCE" if reason == "force_exit" else "NATIVE_RISK"
    return {
        "event_id": event["event_id"],
        "pair": trade["pair"],
        "category": category,
        "native_reason": reason,
        "entry_ms": trade["open_timestamp"],
        "exit_ms": trade["close_timestamp"],
        "source_terminal_asof_exit": terminal,
        "native_risk_price_independently_certified": False,
    }


def trade_path(trade, clocks, prices, events, units):
    orders = trade["orders"]
    times = np.asarray([o["order_filled_timestamp"] for o in orders], dtype=np.int64)
    assert len(times) >= 2 and np.all(np.diff(times) >= 0)
    q = entry = sold = Decimal(0)
    quantities, entries, exits = [0.0], [0.0], [0.0]
    for order in orders:
        amount = Decimal(str(order["amount"]))
        value = amount * Decimal(str(order["safe_price"]))
        if order["ft_is_entry"]:
            q += amount
            entry += value
        else:
            q -= amount
            sold += value
        assert q > Decimal("-1e-8")
        quantities.append(float(q))
        entries.append(float(entry))
        exits.append(float(sold))
    assert abs(q) < Decimal("1e-8"), "Unclosed inventory or unsupported export residual"
    before = np.searchsorted(times, clocks, side="left")
    held = np.asarray(quantities)[before]
    held[clocks > trade["close_timestamp"]] = 0.0
    entry_value, exit_value = np.asarray(entries)[before], np.asarray(exits)[before]
    funding, final_funding, boundaries = funding_history(trade, events, units, clocks)
    sign = -1 if trade["is_short"] else 1
    fees = entry_value * trade["fee_open"] + exit_value * trade["fee_close"]
    reserve = held * prices * trade["fee_close"]
    contribution = sign * (exit_value + held * prices - entry_value) - fees - reserve + funding
    return contribution, held * prices, sign * held * prices, final_funding, boundaries


def periods_for(nav, periods):
    out = [{"name": p["name"], **metrics(nav.loc[p["start"] : p["end"]])} for p in periods]
    assert [p["calendar_hours"] for p in out] == [14592, 8761, 5832]
    assert (
        abs((1 + out[1]["net_return"]) * (1 + out[2]["net_return"]) - 1 - out[0]["net_return"])
        < 1e-12
    )
    return out


def standalone_gate(accounts):
    result = {}
    for capital in (1000, 500):
        group = [a for a in accounts if a["capital"] == capital]
        assert sorted(a["cost_bps"] for a in group) == [8, 15]
        passed = all(
            a["status"] == "VERIFIED"
            and all(
                p["net_return"] > 0
                and p["hourly_mark_drawdown"] <= 0.15
                and p["complete_24h_daily_nav_sharpe"] is not None
                and math.isfinite(p["complete_24h_daily_nav_sharpe"])
                and p["complete_24h_daily_nav_sharpe"] > 0.5
                for p in a["periods"]
            )
            for a in group
        )
        result[str(capital)] = {"both_costs_passed": passed}
    return result


def fixed_combo(hs, nfi, baseline, periods, sleeves_verified):
    assert hs.index.equals(nfi.index) and hs.index.equals(baseline.index)
    assert hs.iloc[0] == nfi.iloc[0] == 500 and baseline.iloc[0] == 1000
    combined = hs + nfi
    result, benchmark = periods_for(combined, periods), periods_for(baseline, periods)
    passed = sleeves_verified and all(p["net_return"] > 0 for p in result)
    passed = passed and result[0]["net_return"] > benchmark[0]["net_return"]
    passed = passed and result[0]["hourly_mark_drawdown"] <= benchmark[0]["hourly_mark_drawdown"]
    return combined, {
        "periods": result,
        "nfi1000_periods": benchmark,
        "actual_initial_capital": 1000,
        "sleeves_independently_verified": sleeves_verified,
        "passed": bool(passed),
        "weight_search_or_linear_scaling": False,
    }


def source_catalog(directory, protocol):
    """Compare the run's complete catalog to the sealed pre-financial identity proof."""
    reference = pd.read_parquet(verified(protocol["source_catalog"]))
    with gzip.open(directory / "source_event_catalog.json.gz", "rt") as stream:
        actual = json.load(stream)
    events = [event for pair in actual for event in actual[pair]]
    assert len(events) == len(reference) == 1891
    mapping = {e["event_id"]: e for e in events}
    assert len(mapping) == len(events)
    date_fields = {
        "confirmation_bar_open",
        "earliest_signal_available",
        "expiry_bar_open",
        "expiry_available",
        "entry_available",
    }
    for ref in reference.to_dict("records"):
        event = mapping[ref["event_id"]]
        for key, expected in ref.items():
            if key in date_fields:
                assert ms(event[key]) == ms(expected), (ref["event_id"], key)
            else:
                assert event[key] == expected, (ref["event_id"], key)
    receipt = read(directory / "source_policy_receipt.json")
    assert receipt["callback_errors"] == [] and receipt["callback_error_count"] == 0
    assert len(receipt["self_terminal_event_ids"]) == 30
    with gzip.open(directory / "source_policy_audit.json.gz", "rt") as stream:
        audit = json.load(stream)
    return mapping, audit


def account(candidate, plan, protocol, destination, hs=True):
    directory = Path(plan["output_root"]) / candidate["name"]
    result_path = directory / "result.json"
    process = read(directory / "process_receipt.json")
    assert process["returncode"] == 0 and process["result_sha256"] == sha(result_path)
    archives = list((directory / "native_results").glob("*.zip"))
    assert len(archives) == 1, "Exactly one native ZIP required"
    with ZipFile(archives[0]) as archive:
        assert archive.testzip() is None
        payloads = [
            json.loads(archive.read(n))
            for n in archive.namelist()
            if n.endswith(".json") and not n.endswith("_config.json")
        ]
        native = [p for p in payloads if "strategy" in p]
        assert len(native) == 1 and list(native[0]["strategy"]) == [candidate["strategy"]]
        stats = native[0]["strategy"][candidate["strategy"]]
    conditional_scope = load_conditional_scope(candidate, directory, archives[0], stats) if not hs else None
    config = read(directory / "config.json")
    capital, fee = candidate["capital"], candidate["cost_bps"] / 10000
    assert config["dry_run"] and config["dry_run_wallet"] == capital
    assert config["fee"] == fee and config["max_open_trades"] == 6
    assert config["tradable_balance_ratio"] == 0.99 and config["stake_amount"] == "unlimited"
    assert config["trading_mode"] == "futures" and config["margin_mode"] == "isolated"
    assert config["timeframe"] == ("1h" if hs else "5m")
    if hs:
        assert config["timeframe_detail"] == "5m"
        assert config["order_types"]["entry"] == config["order_types"]["exit"] == "market"
    assert config["exchange"]["pair_whitelist"] == plan["pairs"] == stats["pairlist"]
    for name, record in candidate["source_files"].items():
        assert sha(directory / "strategies" / name) == record["sha256"]
    grid = pd.date_range(plan["start"], plan["comparison_end"], freq="h").as_unit("ns")
    assert len(grid) == 14592
    frame = pd.read_parquet(directory / "hourly_equity.parquet")
    assert pd.DatetimeIndex(frame.date).as_unit("ns").equals(grid)
    clocks = grid.as_unit("ms").asi8
    total = np.full(len(grid), float(capital))
    gross = np.zeros(len(grid))
    signed = np.zeros(len(grid))
    counts = np.zeros(len(grid), dtype=int)
    closed = np.zeros(len(grid))
    receipts = read(verified(plan["data_receipts"]))
    source_events, policy = source_catalog(directory, protocol) if hs else ({}, [])
    policy_entries = {
        r["event_id"] for r in policy if r["status"] == "ENTRY_CONFIRMED_INTERFACE_ONLY"
    }
    cash_rows, funding_rows, source_rows, boundary_rows, residuals = [], [], [], [], []
    used_tags = set()
    input_hashes = {}
    by_pair = {
        p: [(i, t) for i, t in enumerate(stats["trades"]) if t["pair"] == p] for p in plan["pairs"]
    }
    assert sum(map(len, by_pair.values())) == len(stats["trades"])
    for pair in plan["pairs"]:
        if not by_pair[pair]:
            continue
        stem = pair.replace("/", "_").replace(":", "_")
        frames = {}
        for kind in ("1h-mark", "1h-funding_rate", "1h-futures"):
            relative = "futures/" + stem + "-" + kind + ".feather"
            path = Path(plan["datadir"]) / relative
            assert sha(path) == receipts[relative]
            frames[kind] = pd.read_feather(path)
            input_hashes[str(path)] = receipts[relative]
        marks = frames["1h-mark"].set_index("date").open
        fund = frames["1h-funding_rate"]
        events = pd.DatetimeIndex(fund.date).as_unit("ms").asi8
        units = fund.funding_rate.to_numpy() * marks.reindex(fund.date).to_numpy()
        prices = marks.reindex(grid).to_numpy()
        assert np.isfinite(prices).all() and np.isfinite(units).all()
        assert np.all(np.diff(events) > 0) and np.all(events % HOUR == 0)
        for number, trade in by_pair[pair]:
            assert trade["leverage"] == 1 and trade["fee_open"] == trade["fee_close"] == fee
            row = cash_from_orders(trade)
            row["trade_index"] = number
            cash_rows.append(row)
            if Decimal(row["quantity_residual"]):
                residuals.append({"trade_index": number, "quantity": row["quantity_residual"]})
            path, g, s, terminal, boundaries = trade_path(trade, clocks, prices, events, units)
            total += path
            gross += g
            signed += s
            counts += (clocks > trade["open_timestamp"]) & (clocks <= trade["close_timestamp"])
            closed += (clocks > trade["close_timestamp"]) * float(row["net_pnl"])
            funding_rows.append(
                {
                    "trade_index": number,
                    "reconstructed": terminal,
                    "native": trade["funding_fees"],
                    "error": abs(terminal - trade["funding_fees"]),
                }
            )
            source_row = None
            if hs:
                tag = trade["enter_tag"]
                assert tag not in used_tags and tag in policy_entries
                used_tags.add(tag)
                source_row = source_trade_check(
                    trade, source_events[tag], frames["1h-futures"], ms(plan["comparison_end"])
                )
                source_rows.append({"trade_index": number, **source_row})
                # A second independent formula exists only for the strict H&S two-fill path.
                strict_path, strict_final = two_fill_funding(trade, events, units, clocks)
                replay_path, _, _ = funding_history(trade, events, units, clocks)
                assert np.max(np.abs(strict_path - replay_path)) < 1e-7
                assert abs(strict_final - terminal) < 1e-7
            evidence = boundary_evidence(trade, set(events.tolist()), strict_hs_proven=hs)
            if not hs:
                evidence = supplement_boundary_evidence(candidate, number, trade, evidence, conditional_scope)
            boundary_rows.append(
                {"trade_index": number, "pair": pair, **evidence, "replayed_boundaries": boundaries}
            )
    verify_scope_coverage(conditional_scope, boundary_rows)
    observed = frame.hourly_mark_equity.to_numpy()
    errors = total - observed
    nav = pd.Series(total, index=grid)
    periods = periods_for(nav, plan["periods"])
    missing = [missing_exposure(stats["trades"], p) for p in ("AXS/USDT:USDT", "TRB/USDT:USDT")]
    maximum_cash = max((float(r["error"]) for r in cash_rows), default=0)
    maximum_funding = max((r["error"] for r in funding_rows), default=0)
    checks = {
        "hourly_nav": float(np.max(np.abs(errors))) < 3e-5,
        "campaign_cash": maximum_cash < 1e-5,
        "aggregate_cash": abs(
            math.fsum(float(r["net_pnl"]) for r in cash_rows) - stats["profit_total_abs"]
        )
        < 1e-4,
        "native_funding_replay": maximum_funding < 1e-7,
        "open_campaigns": bool(np.array_equal(counts, frame.open_trades.to_numpy())),
        "closed_campaign_cash_path": float(np.max(np.abs(closed - frame.closed_realized_pnl)))
        < 1e-4,
        "terminal_cash": abs(stats["final_balance"] - capital - stats["profit_total_abs"]) < 1e-6,
        "source_event_exit_checks": len(source_rows) == len(stats["trades"]) if hs else True,
    }
    uncertainty = []
    if any(r["status"] == "UNKNOWN" for r in boundary_rows):
        uncertainty.append("ZIP_BOUNDARY_ORDER_CREATION_UNKNOWN")
    if any(r["status"] == "UNKNOWN" for r in missing):
        uncertainty.append("AXS_TRB_MISSING_RATE_GROSS_EXPOSURE")
    status = "FAILED" if not all(checks.values()) else "UNKNOWN" if uncertainty else "VERIFIED"
    result = {
        "candidate": candidate["name"],
        "capital": capital,
        "cost_bps": candidate["cost_bps"],
        "status": status,
        "passed": status == "VERIFIED",
        "checks": checks,
        "unknown_reasons": uncertainty,
        "periods": periods,
        "trades": len(stats["trades"]),
        "fills": sum(len(t["orders"]) for t in stats["trades"]),
        "maximum_nav_error_usdt": float(np.max(np.abs(errors))),
        "maximum_cash_error_usdt": maximum_cash,
        "maximum_funding_error_usdt": maximum_funding,
        "maximum_gross_to_nav": float(np.max(gross / total)),
        "maximum_abs_net_to_nav": float(np.max(np.abs(signed) / total)),
        "terminal_balance_outside_ranking_nav": stats["final_balance"],
        "common_endpoint_open_campaigns": int(counts[-1]),
        "native_forced_exits": sum(t["exit_reason"] == "force_exit" for t in stats["trades"]),
        "total_commission": str(sum((Decimal(r["commission"]) for r in cash_rows), Decimal(0))),
        "unknown_funding": missing,
        "serialization_quantity_residuals": residuals,
        "archive_sha256": sha(archives[0]),
        "observed_nav_sha256": sha(directory / "hourly_equity.parquet"),
        "input_hashes": input_hashes,
        "boundary_evidence_supplement": account_evidence_fields(conditional_scope, boundary_rows, status),
        "conditional_old_execution_model": True,
        "historical_rules_certified": False,
        "production_eligible": False,
    }
    destination.mkdir(parents=True, exist_ok=False)
    for name, rows in [
        ("cash_rows", cash_rows),
        ("funding_rows", funding_rows),
        ("source_exit_rows", source_rows),
        ("boundary_rows", boundary_rows),
    ]:
        save(destination / (name + ".json"), rows)
    pd.DataFrame(
        {
            "date": grid,
            "reconstructed_nav": total,
            "observed_nav": observed,
            "difference": errors,
            "gross": gross,
            "signed": signed,
        }
    ).to_parquet(destination / "nav.parquet", index=False)
    save(destination / "audit.json", result)
    return result, nav


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--completed-output-audit", action="store_true", required=True)
    parser.parse_args()
    frozen = read(HERE / "independent_audit_freeze_v3.json")
    for path, digest in frozen["files"].items():
        assert sha(path) == digest, path
    protocol = read(BASE_STUDY / "independent_audit_protocol.json")
    assert read(verified(protocol["input_validation"]))["passed"]
    assert read(verified(protocol["precision_validation"]))["passed"]
    plan = read(BASE_STUDY / "plan.json")
    assert (BASE_STUDY / "execution_complete.json").exists(), (
        "Future four native wallets must finish first"
    )
    nfi_plan = read(verified(plan["nfi500_dependency"]["plan"]))
    assert Path(plan["nfi500_dependency"]["execution_complete_path"]).exists()
    output = HERE / "independent_audit_outputs_v3"
    output.mkdir(exist_ok=False)
    accounts, paths = [], {}
    for candidate in plan["candidates"]:
        result, nav = account(candidate, plan, protocol, output / candidate["name"])
        accounts.append(result)
        paths[candidate["name"]] = nav
    nfi_results, nfi_paths = {}, {}
    for candidate in nfi_plan["candidates"]:
        result, nav = account(candidate, nfi_plan, protocol, output / candidate["name"], hs=False)
        nfi_results[candidate["cost_bps"]] = result
        nfi_paths[candidate["cost_bps"]] = nav
    combos = []
    for baseline in plan["benchmark_nfi1000"]:
        cost = baseline["cost_bps"]
        name = f"hs40_500_cost{cost}"
        frame = pd.read_parquet(verified(baseline["hourly_curve"]))
        curve = pd.Series(
            frame.hourly_mark_equity.to_numpy(), index=pd.DatetimeIndex(frame.date).as_unit("ns")
        )
        hs_result = next(a for a in accounts if a["candidate"] == name)
        nav, result = fixed_combo(
            paths[name],
            nfi_paths[cost],
            curve,
            plan["periods"],
            hs_result["passed"] and nfi_results[cost]["passed"],
        )
        result["cost_bps"] = cost
        combos.append(result)
        nav.rename("fixed_combo_nav").to_frame().to_parquet(
            output / f"fixed_combo_cost{cost}.parquet"
        )
    save(
        output / "admission.json",
        {
            "accounts": accounts,
            "standalone": standalone_gate(accounts),
            "fixed_combo": combos,
            "both_cost_combo_passed": all(c["passed"] for c in combos),
            "financial_reruns": 0,
            "goal_complete": False,
            "production_eligible": False,
        },
    )


if __name__ == "__main__":
    main()
