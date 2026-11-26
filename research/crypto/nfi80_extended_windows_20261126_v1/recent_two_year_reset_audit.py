"""Compare the 2026 native 12-slot path with and without a January account reset."""

from math import fsum, isclose
from pathlib import Path
from statistics import median

import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.holdout_accounts import invariant_config
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .annual_trade_scale import FIVE_MINUTES, describe
from .capacity_2024_drawdown_attribution import worst_drawdown
from .inputs import HERE
from .recent_two_year_attribution import ARCHIVE, MARK_TOLERANCE, ledger


def config_terms(config):
    core = invariant_config(config)
    for key in ("datadir", "pairlists", "pairs", "timerange"):
        core.pop(key, None)
    core["exchange"] = {key: value for key, value in core["exchange"].items() if key != "pair_whitelist"}
    if "original_config" in core:
        core["original_config"] = config_terms(core["original_config"])
    return core


def plan_receipt(wallet):
    path = checked(wallet["refs"][".zip"])
    return receipt(path.parents[1] / "registered_plan.json")


def common_basis(reset, carry):
    a, b = (read(checked(wallet["plan"])) for wallet in (reset, carry))
    for key in ("engine_commit", "engine_manifest", "observation_frequency", "allowed_leverage", "cost_model"):
        assert a[key] == b[key], key
    a_case, b_case = a["candidates"][0], b["candidates"][0]
    assert a_case["source_files"] == b_case["source_files"] and a_case["max_open_trades"] == b_case["max_open_trades"] == 12
    assert config_terms(read(checked(reset["config"]))) == config_terms(read(checked(carry["config"])))
    source = b["data_receipts"]
    manifest = read(checked(source))
    days_before, days_after = (read(checked(plan["historical_schedule"]))["days"] for plan in (a, b))
    assert all(days_after[day] == row for day, row in days_before.items())
    old = read(checked(a["data_receipts"]))
    source_stops = set()
    for name, sha in old.items():
        shard, = [row for row in manifest["files"][name] if row["source_year"] == "2026"]
        assert sha == shard["sha256"] and Path(shard["path"]) == Path(a["datadir"]) / name
        assert shard["start_inclusive"] == a["start"]
        source_stops.add(shard["stop_exclusive"])
    seam, = source_stops
    old_markets, new_markets = (read(checked(plan["market_snapshot"])) for plan in (a, b))
    fields = ("id", "symbol", "settle", "swap", "linear", "inverse", "contractSize", "precision", "limits", "active")
    for pair in a["pairs"]:
        assert all(old_markets["markets"][pair][key] == new_markets["markets"][pair][key] for key in fields)
        assert old_markets["leverage_tiers"][pair] == new_markets["leverage_tiers"][pair]
    return pd.Timestamp(a["start"]), pd.Timestamp(seam) - FIVE_MINUTES, {
        "same_engine_commit": a["engine_commit"], "bound_market_manifest": source,
        "matched_pairlist_days": len(days_before), "matched_source_files": len(old), "matching_market_terms": len(a["pairs"]),
        "same_registered_trading_config": True, "matched_source_grid_stops_before": seam,
    }


def observe(wallet, start, stop):
    curve = wallet["curve"].loc[start:stop]
    first, last = curve.iloc[0], curve.iloc[-1]
    lower, upper = (timestamp.value // 1_000_000 for timestamp in (start, stop))
    trades = stats_from(checked(wallet["refs"][".zip"]))["trades"]
    realized = {}
    for trade in trades:
        if trade["exit_reason"] != "force_exit" and lower <= trade["close_timestamp"] < upper:
            key = (trade["pair"], trade["is_short"], pd.Timestamp(trade["open_timestamp"], unit="ms", tz="UTC"))
            realized[key] = wallet["trades"][key]
    net = float(last.marked_equity - first.marked_equity)
    closed = fsum(row["profit"] for row in realized.values())
    assert isclose(closed, float(last.closed_realized_pnl - first.closed_realized_pnl),
                   abs_tol=MARK_TOLERANCE, rel_tol=0)
    empty_origin = pd.isna(first.open_pnl)
    if empty_origin:
        assert first.open_trades == 0 and first.equity == first.marked_equity == 1000
    initial_open_pnl = 0.0 if empty_origin else first.open_pnl
    open_mark_change = float((last.open_pnl + last.marked_equity - last.equity)
                             - (initial_open_pnl + first.marked_equity - first.equity))
    assert isclose(net, closed + open_mark_change, abs_tol=MARK_TOLERANCE, rel_tol=0)
    peak, trough, dd = worst_drawdown(curve.marked_equity)
    return {
        "curve": curve, "initial_nav": float(first.marked_equity), "ending_nav": float(last.marked_equity),
        "net_return": float(net / first.marked_equity), "marked_drawdown": dd,
        "drawdown_peak": peak.isoformat(), "drawdown_trough": trough.isoformat(),
        "open_trades_start": int(first.open_trades), "open_trades_end": int(last.open_trades),
        "empty_initial_open_pnl_field": bool(empty_origin),
        "net_realized_pnl": closed, "change_in_open_marked_pnl": open_mark_change,
        "trades": realized, "tail_counts": describe(realized.values()),
    }


def classify(reset, carry):
    a, b = reset["trades"], carry["trades"]
    matched = a.keys() & b.keys()
    exact = {key for key in matched if a[key]["fill_signature"] == b[key]["fill_signature"]}
    pieces = {}
    for name, ka, kb in (
        ("same_fill_clocks_prices", exact, exact),
        ("same_first_entry_other_fills", matched - exact, matched - exact),
        ("first_entry_only_one_wallet", a.keys() - matched, b.keys() - matched),
    ):
        pnl_a = fsum(a[key]["profit"] for key in sorted(ka)) / reset["initial_nav"]
        pnl_b = fsum(b[key]["profit"] for key in sorted(kb)) / carry["initial_nav"]
        pieces[name] = {"reset_count": len(ka), "carry_count": len(kb), "reset_nav_fraction": pnl_a,
                        "carry_nav_fraction": pnl_b, "carry_minus_reset": pnl_b - pnl_a}
    start_open = carry["change_in_open_marked_pnl"] / carry["initial_nav"]
    reset_open = reset["change_in_open_marked_pnl"] / reset["initial_nav"]
    delta = carry["net_return"] - reset["net_return"]
    assert isclose(delta, fsum(row["carry_minus_reset"] for row in pieces.values()) + start_open - reset_open,
                   abs_tol=1e-10, rel_tol=0)
    first_match = min(exact, key=lambda key: (key[2], key[0], key[1]))
    return {"realized_trade_groups": pieces, "open_marked_pnl_change_gap_fraction": start_open - reset_open,
            "normalized_return_gap": delta, "first_matched_entry": {
                "pair": first_match[0], "clock": first_match[2].isoformat(),
                "reset_margin_usdt": a[first_match]["opening_margin"], "carry_margin_usdt": b[first_match]["opening_margin"],
            }, "same_fill_median_margin_ratio": median(b[key]["opening_margin"] / a[key]["opening_margin"] for key in exact)}


def main():
    reset_ref = receipt(ARCHIVE / "comparison_15bp.json")
    carry_ref = receipt(HERE / "recent_two_year_15bp_12_verified.json")
    reset_summary = read(checked(reset_ref))["control"]
    carry_account = read(checked(carry_ref))["account"]
    carry_summary = read(checked(carry_account["proof"]))
    venues = {}
    for name, summary in (("reset", reset_summary), ("carry", carry_summary)):
        wallet = ledger(summary)
        config, = [ref for ref in summary["sources"] if Path(ref["path"]).name == "effective_config.json"]
        venues[name] = {**wallet, "config": config, "plan": plan_receipt(wallet)}
    start, stop, basis = common_basis(venues["reset"], venues["carry"])
    reset, carry = (observe(venues[name], start, stop) for name in ("reset", "carry"))
    grid = pd.date_range(start, stop, freq=FIVE_MINUTES).as_unit("ns")
    assert all(wallet["curve"].index.as_unit("ns").equals(grid) for wallet in (reset, carry))
    attribution = classify(reset, carry)
    trough = carry["drawdown_trough"]
    reset_equity = reset["curve"].marked_equity
    reset_on_carry_drawdown = float(reset_equity.at[trough] / reset_equity.at[carry["drawdown_peak"]] - 1)
    out = {"source": receipt(Path(__file__)), "reset_native_reconciliation": reset_ref, "carry_native_reconciliation": carry_ref,
           "common_basis": basis, "start": start.isoformat(), "end": stop.isoformat(), "five_minute_marks": len(reset["curve"]),
           "wallets": {name: {key: value for key, value in wallet.items() if key not in ("curve", "trades")}
                       for name, wallet in (("reset", reset), ("carry", carry))},
           "reset_return_during_carry_peak_to_trough": reset_on_carry_drawdown, "attribution": attribution,
           "scope": "Observed path comparison. January capital and holdings differ jointly, and the warmup uses separately sourced pre-January tapes. "
                    "Contributions are marked-account arithmetic using each path's own January NAV, without causal counterfactual claims.",
           "new_native_accounts": 0, "orders_enabled": False}
    write_once(HERE / "recent_two_year_reset_audit.json", out)
    print(stop.isoformat(), reset["net_return"], carry["net_return"], reset["marked_drawdown"], carry["marked_drawdown"], flush=True)


if __name__ == "__main__":
    main()
