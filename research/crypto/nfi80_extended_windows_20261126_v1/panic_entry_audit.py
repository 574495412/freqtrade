"""Map an existing panic rule to reconciled native NFI long entries."""

import argparse
import runpy
from math import fsum
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .continuous_native_inputs import IndependentShardRead, candle_filename
from .inputs import HERE
from .recent_two_year_attribution import ledger
from .volatility_across_years import selected_receipt


HOUR = pd.Timedelta(hours=1)
FIRST_DAY = pd.Timestamp("2025-10-10T00:00:00Z")
LAST_DAY = pd.Timestamp("2025-10-12T00:00:00Z")
SOURCE = (
    HERE.parents[3] / "Vibe-Trading/agent/research/crypto_panic_rebound_20261126_v1"
)
BTC = "BTC/USDT:USDT"


def bind():
    original_freeze = receipt(SOURCE / "freeze.json")
    original_sources = read(checked(original_freeze))["source_receipts"]
    study = next(row for row in original_sources if Path(row["path"]) == SOURCE / "study.py")
    checked(study)
    reference = {
        "protocol": receipt(HERE / "panic_entry_protocol.json"),
        "script": receipt(Path(__file__)),
        "market_rule_receipt": study,
        "market_rule_freeze": original_freeze,
        "market_rule_sources": original_sources,
        "stress": receipt(HERE / "stress_campaign_comparison.json"),
        "annual": receipt(HERE / "annual_2024_boost14_15bp_verified.json"),
        "recent": receipt(HERE / "recent_two_year_15bp_comparison.json"),
        "fill_outcomes_mapped_at_freeze": False,
        "orders_enabled": False,
    }
    return write_once(HERE / "panic_entry_freeze.json", reference), reference


def close_panel(plan, membership, grid):
    data = read(checked(plan["data_receipts"]))
    shards = IndependentShardRead(plan["data_receipts"]) if "files" in data else None
    requested = grid.union(grid - 4 * HOUR).union(grid - HOUR)
    closes = {}
    for pair in membership.columns:
        if shards is not None:
            frame = shards(pair, "1h-futures")
        else:
            name = candle_filename(pair, "1h-futures")
            source = {"path": str(Path(plan["datadir"]) / name), "sha256": data[name]}
            frame = pd.read_feather(checked(source), columns=["date", "close", "volume"])
        dates = pd.DatetimeIndex(frame["date"]).as_unit("ns")
        if not dates.is_unique or not dates.equals(dates.floor("h")):
            raise ValueError(f"Invalid hourly candle clocks for {pair}")
        traded = np.isfinite(frame["close"]) & frame["close"].gt(0) & frame["volume"].gt(0)
        prices = pd.Series(frame["close"].where(traded).to_numpy(), index=dates + HOUR)
        closes[pair] = prices.reindex(requested)
    return pd.DataFrame(closes)


def observed_events(plan, fills, evaluate):
    grid = pd.DatetimeIndex(sorted({clock.floor("h") for _, _, clock in fills})).as_unit("ns")
    days = read(checked(plan["historical_schedule"]))["days"]
    membership = []
    for day in grid.strftime("%Y-%m-%d"):
        entry = days[day]
        if pd.Timestamp(entry["latest_input_close"]) >= pd.Timestamp(day, tz="UTC"):
            raise ValueError(f"Potential look-ahead in the daily pool at {day}")
        pairs = set(entry["pairs"])
        if len(pairs) != 80:
            raise ValueError(f"Unexpected pool size at {day}")
        membership.append(dict.fromkeys(pairs - {BTC}, True))
    active = pd.DataFrame(membership, index=grid).eq(True)
    closes = close_panel(plan, active, grid)
    _, events = evaluate(grid, closes, active)
    midpoint = grid[len(grid) // 2]
    _, prefix = evaluate(grid[grid <= midpoint], closes.loc[:midpoint], active.loc[:midpoint])
    pd.testing.assert_frame_equal(events.loc[:midpoint], prefix, check_exact=True)
    return events


def observed_trades(fills, events):
    rows = []
    for (pair, _, clock), trade in sorted(fills.items()):
        hour = clock.floor("h")
        event = events.loc[hour]
        nav = trade["equity_before_entry"]
        pnl = trade["profit"]
        rows.append({
            "pair": pair,
            "first_fill": clock.isoformat(),
            "shock_end": (hour - HOUR).isoformat(),
            "observed_peers": int(event["observed"]),
            "shock": bool(event["shock_event"]),
            "exceptional_signal_window": bool(hour - 4 * HOUR < LAST_DAY and hour >= FIRST_DAY),
            "profit_usdt": pnl,
            "profit_to_account_before_entry": pnl / nav if nav and nav > 0 else None,
        })
    return rows


def summarize(rows):
    hit = [row for row in rows if row["shock"]]
    fractions = [row["profit_to_account_before_entry"] for row in hit]
    return {
        "observed_longs": len(rows),
        "unknown_shock": sum(row["observed_peers"] < 64 for row in rows),
        "shock_hits": len(hit),
        "shock_hit_pnl_usdt": fsum(row["profit_usdt"] for row in hit),
        "shock_hit_large_wins": sum(value is not None and value >= .01 for value in fractions),
        "shock_hit_large_losses": sum(value is not None and value <= -.01 for value in fractions),
        "shock_hits_without_prior_nav": fractions.count(None),
        "hit_fills": hit,
    }


def screen(accounts, evaluate):
    fills = {
        role: {key: trade for key, trade in ledger(account)["trades"].items() if not key[1]}
        for role, (account, _) in accounts.items()
    }
    plans = {role: read(checked(ref)) for role, (_, ref) in accounts.items()}
    for field in ("historical_schedule", "data_receipts"):
        if plans["12"][field] != plans["14"][field]:
            raise ValueError(f"Expected matched {field}")
    events = observed_events(plans["14"], fills["12"].keys() | fills["14"].keys(), evaluate)
    return {role: observed_trades(rows, events) for role, rows in fills.items()}


def select_annual(source):
    return {
        role: (account, selected_receipt(account["receipts"], "/registered_plan.json"))
        for role, account in (("12", source["fixed_boost_12"]), ("14", source["boost_14"]))
    }


def select_recent(source):
    return {
        role: (read(checked(account["proof"])), account["registered_plan"])
        for role, account in source.items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args()
    frozen, source = bind()
    if args.freeze_only:
        print(frozen)
        return
    for row in source["market_rule_sources"]:
        if row["path"].endswith("/study.py"):
            checked(row)
    original = runpy.run_path(str(checked(source["market_rule_receipt"])))
    annual = read(checked(source["annual"]))
    recent = read(checked(source["recent"]))
    if (annual["cost_per_side_bp"], recent["cost_bps_per_side"]) != (15, 15):
        raise ValueError("This screen requires the matched 15 bp reports")
    earlier = screen(select_annual(annual["accounts"]), original["event_inputs"])
    current = screen(select_recent(recent["accounts"]), original["event_inputs"])
    saved_stress = read(checked(source["stress"]))["entry_occupancy"]["stress_2024"]
    stress_keys = {(row["pair"], row["first_fill"]) for row in saved_stress}
    stress = [row for row in earlier["14"] if (row["pair"], row["first_fill"]) in stress_keys]
    if len(stress) != len(stress_keys):
        raise ValueError("The original stress fills are missing from the natural campaign ledger")
    years = {
        role: {
            year: summarize([row for row in rows if row["first_fill"].startswith(year)
                             and not row["exceptional_signal_window"]])
            for year in ("2025", "2026")
        } for role, rows in current.items()
    }
    stress_hit = any(row["shock"] and row["pair"].split("/")[0] in {"GALA", "MANTA"}
                     for row in stress)
    passed = stress_hit and all(
        row["shock_hit_large_losses"] > 0 and row["shock_hit_large_wins"] == 0
        and row["shock_hit_pnl_usdt"] < 0 and row["shock_hits_without_prior_nav"] == 0
        for row in years["14"].values()
    )
    write_once(HERE / "panic_entry_audit.json", {
        "freeze": frozen,
        "annual_2024": {role: summarize(rows) for role, rows in earlier.items()},
        "stress_14": stress,
        "primary_years": years,
        "exceptional_signal_window": {
            role: summarize([row for row in rows if row["exceptional_signal_window"]])
            for role, rows in current.items()
        },
        "stress_hit_for_slots_11_or_12": stress_hit,
        "advance_to_native_account_evaluation": passed,
        "new_native_accounts": 0,
        "orders_enabled": False,
    })
    print("Native account admission:", passed)


if __name__ == "__main__":
    main()
