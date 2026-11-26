"""Audit two-year long/short contributions and the panic-entry trade paths."""

from functools import lru_cache
from math import fsum, isclose
from pathlib import Path
from statistics import median

import numpy as np
import pandas as pd

from research.crypto.head_shoulders_financial_native_20261117_v3 import independent_account_audit_v2
from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .annual_trade_scale import FIVE_MINUTES, describe
from .capacity_2024_drawdown_attribution import market_streams
from .continuous_native_inputs import IndependentShardRead
from .inputs import HERE
from .panic_entry_audit import select_annual, select_recent
from .recent_two_year_attribution import MARK_TOLERANCE, ledger


def timestamp(milliseconds):
    return pd.Timestamp(milliseconds, unit="ms", tz="UTC")


def books(account):
    wallet = ledger(account)
    stats = stats_from(checked(wallet["refs"][".zip"]))
    native = {}
    for trade in stats["trades"]:
        key = (trade["pair"], trade["is_short"], timestamp(trade["open_timestamp"]))
        if key in native:
            raise ValueError(f"Multiple exported campaigns match {key}")
        native[key] = trade
    return wallet, native


def direction_totals(wallet, native):
    rows = {}
    for name, short in (("long", False), ("short", True)):
        natural = {key: row for key, row in wallet["trades"].items() if key[1] == short}
        forced = [trade for key, trade in native.items()
                  if key[1] == short and trade["exit_reason"] == "force_exit"]
        profit = fsum(row["profit"] for row in natural.values())
        worst = min(natural, key=lambda key: natural[key]["profit"])
        event = native[worst]
        tags = sorted({native[key]["enter_tag"].strip() for key in natural})
        tagged = {
            tag: [row["profit"] for key, row in natural.items()
                  if native[key]["enter_tag"].strip() == tag]
            for tag in tags
        }
        rows[name] = {
            "natural_closes": describe(natural.values()),
            "natural_profit_usdt": profit,
            "natural_wins": sum(row["profit"] > 0 for row in natural.values()),
            "by_entry_tag": {
                tag: {"count": len(returns), "net_usdt": fsum(returns)}
                for tag, returns in tagged.items()
            },
            "worst_natural_close": {
                "pair": event["pair"],
                "first_fill": worst[-1].isoformat(),
                "tag": event["enter_tag"].strip(),
                "additional_entry_fills": natural[worst]["entry_fills"] - 1,
                "profit_usdt": natural[worst]["profit"],
            },
            "forced_at_end": len(forced),
            "forced_exit_value_usdt": fsum(trade["profit_abs"] for trade in forced),
        }
    if not isclose(fsum(row["natural_profit_usdt"] for row in rows.values()),
                   wallet["curve"].closed_realized_pnl.iloc[-1],
                   rel_tol=0, abs_tol=MARK_TOLERANCE):
        raise ValueError("Long/short net PnL does not reconcile to the original cash ledger")
    return rows


def price_reader(plan):
    fingerprints = read(checked(plan["data_receipts"]))
    shards = IndependentShardRead(plan["data_receipts"]) if "files" in fingerprints else None

    @lru_cache(maxsize=6)
    def get(pair):
        if shards is None:
            return market_streams(plan, fingerprints, pair)
        marks = shards(pair, "5m-mark").set_index("date").open
        rates = shards(pair, "1h-funding_rate").set_index("date").funding_rate
        hourly = shards(pair, "1h-mark").set_index("date").open
        funding_units = (rates * hourly.reindex(rates.index)).to_numpy()
        return marks, rates.index.as_unit("ms").asi8, funding_units

    return get


def excursion(wallet, native, prices, row):
    pair, opened = row["pair"], pd.Timestamp(row["first_fill"])
    key = (pair, False, opened)
    trade = native[key]
    nav = wallet["trades"][key]["equity_before_entry"]
    if nav is None or nav <= 0:
        raise ValueError(f"Missing positive pre-entry NAV for {pair} {opened}")
    closed = timestamp(trade["close_timestamp"])
    held = wallet["curve"].loc[opened + FIVE_MINUTES:closed].index.as_unit("ns")
    expected = pd.date_range(opened + FIVE_MINUTES, closed, freq=FIVE_MINUTES).as_unit("ns")
    if not held.equals(expected):
        raise ValueError(f"Missing account marks between first fill and close for {pair} {opened}")
    dates = held.insert(len(held), closed + FIVE_MINUTES)
    clocks = dates.as_unit("ms").asi8
    marks, events, units = prices(pair)
    observed = marks.reindex(held).to_numpy()
    known = (events >= trade["open_timestamp"]) & (events <= trade["close_timestamp"])
    if not (np.isfinite(observed) & (observed > 0)).all():
        raise ValueError(f"Missing market mark during open campaign {pair} {opened}")
    if not np.isfinite(units[known]).all():
        raise ValueError(f"Missing funding marks for {pair} {opened}")
    result, _, _, funding, _ = independent_account_audit_v2.trade_path(
        trade, clocks, np.append(observed, 0), events[known], units[known],
    )
    if not isclose(funding, trade["funding_fees"], rel_tol=0, abs_tol=1e-7):
        raise ValueError(f"Funding mismatch during {pair} {opened}")
    if not isclose(result[-1], row["profit_usdt"], rel_tol=0, abs_tol=MARK_TOLERANCE):
        raise ValueError(f"Terminal natural-close PnL does not match for {pair} {opened}")
    held_result = result[:-1]
    losses = held_result <= -.01 * nav
    worst = float(min(0, min(held_result, default=0.0)))
    return {
        "pair": pair,
        "first_fill": opened.isoformat(),
        "natural_close": closed.isoformat(),
        "net_profit_usdt": row["profit_usdt"],
        "net_profit_as_fraction_of_prior_nav": row["profit_to_account_before_entry"],
        "lowest_held_mark_pnl_usdt": worst,
        "lowest_held_mark_pnl_as_fraction_of_prior_nav": worst / nav,
        "lowest_held_mark_clock": held[held_result.argmin()].isoformat() if worst < 0 else None,
        "first_mark_at_or_below_minus_1pct_of_prior_nav": (
            held[losses.argmax()].isoformat() if losses.any() else None
        ),
    }


def excursion_totals(rows):
    large = [row for row in rows if row["net_profit_as_fraction_of_prior_nav"] >= .01]
    return {
        "campaigns_checked": len(rows),
        "large_winners": len(large),
        "median_lowest_held_mark_pnl_for_large_winners": median(
            row["lowest_held_mark_pnl_as_fraction_of_prior_nav"] for row in large
        ),
        "any_campaign_ever_below_minus_1pct": sum(
            row["first_mark_at_or_below_minus_1pct_of_prior_nav"] is not None for row in rows
        ),
        "large_winners_ever_below_minus_1pct": sum(
            row["first_mark_at_or_below_minus_1pct_of_prior_nav"] is not None for row in large
        ),
    }


def main():
    source = receipt(HERE / "panic_entry_audit.json")
    screened = read(checked(source))
    frozen = read(checked(screened["freeze"]))
    recent = select_recent(read(checked(frozen["recent"]))["accounts"])
    annual = select_annual(read(checked(frozen["annual"]))["accounts"])
    near = {role: books(account) for role, (account, _) in recent.items()}
    sides = {role: direction_totals(*book) for role, book in near.items()}
    prices = price_reader(read(checked(recent["14"][1])))
    paths = [
        excursion(*near["14"], prices, row)
        for year in screened["primary_years"]["14"].values()
        for row in year["hit_fills"]
    ]
    past = books(annual["14"][0])
    annual_prices = price_reader(read(checked(annual["14"][1])))
    stress = [excursion(*past, annual_prices, row) for row in screened["stress_14"]]
    summary = excursion_totals(paths)
    write_once(HERE / "native_trade_path_review.json", {
        "source": receipt(Path(__file__)),
        "panic_entry_screen": source,
        "continuous_accounts": frozen["recent"],
        "earlier_accounts": frozen["annual"],
        "continuous_realized_by_side": sides,
        "14_slot_panic_trade_summary": summary,
        "14_slot_panic_trade_paths": paths,
        "14_slot_stress_trade_paths": stress,
        "scope": (
            "Realized contribution and exact observable five-minute paths on native fills. "
            "First-fill NAV is the preceding five-minute mark. Position PnL while held includes "
            "partial fills, native funding, and reserved exit cost. No exit or position changes."
        ),
        "orders_enabled": False,
    })
    print(summary)


if __name__ == "__main__":
    main()
