"""Measure native concurrent entry margin on the Top Coins and Rebuy long paths."""

import ast
from math import fsum
from pathlib import Path
from statistics import median

import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .annual_trade_scale import FIVE_MINUTES
from .inputs import HERE
from .recent_two_year_attribution import ledger, reports
from .scalp_margin import quantify


FAMILIES = {"Top Coins": "long_top_coins_mode_tags", "Rebuy": "long_rebuy_mode_tags"}
EXACT = {"Top Coins": "145", "Rebuy": "63"}


def mode_tags(path):
    cls, = (node for node in ast.parse(path.read_text()).body
            if isinstance(node, ast.ClassDef) and node.name == "NostalgiaForInfinityX8")
    names = set(FAMILIES.values())
    assignments = {target.id: ast.literal_eval(node.value)
                   for node in cls.body if isinstance(node, ast.Assign)
                   for target in node.targets if isinstance(target, ast.Name) and target.id in names}
    tags = {family: set(assignments[field]) for family, field in FAMILIES.items()}
    assert all(EXACT[family] in codes for family, codes in tags.items())
    assert not tags["Top Coins"] & tags["Rebuy"]
    return tags


def summarize(rows):
    natural = [row for row in rows if row["exit_reason"] != "force_exit"]
    crossed = [row for row in natural if row["first_crossed_ms"] is not None]
    known = [row for row in crossed if row["net_fraction_prior_nav"] is not None]
    fractions = [row["net_fraction_prior_nav"] for row in known]
    return {
        "natural_closes": len(natural), "terminal_campaigns": len(rows) - len(natural),
        "natural_lifetime_above_2x": sum(row["lifetime_multiple_of_first_margin"] > 2 for row in natural),
        "natural_concurrent_above_2x": len(crossed),
        "terminal_concurrent_above_2x": sum(row["first_crossed_ms"] is not None and row["exit_reason"] == "force_exit"
                                             for row in rows),
        "concurrent_above_2x_realized_pnl_usdt": fsum(row["realized_pnl_usdt"] for row in crossed),
        "concurrent_above_2x_1pct_winners": sum(x >= .01 for x in fractions),
        "concurrent_above_2x_1pct_losses": sum(x <= -.01 for x in fractions),
        "concurrent_above_2x_missing_prior_nav": len(crossed) - len(known),
        "natural_1pct_winners": sum(row["net_fraction_prior_nav"] is not None and row["net_fraction_prior_nav"] >= .01
                                    for row in natural),
        "median_peak_multiple": median(row["peak_multiple_of_first_margin"] for row in natural) if natural else None,
        "maximum_peak_multiple": max((row["peak_multiple_of_first_margin"] for row in rows), default=None),
    }


def inspect(summary, tags, strategy_source):
    account = ledger(summary)
    archive = checked(account["refs"][".zip"])
    plan_ref = receipt(archive.parents[1] / "registered_plan.json")
    plan = read(checked(plan_ref))
    assert plan["candidates"][0]["source_files"]["NostalgiaForInfinityX8.py"] == strategy_source
    rows = {family: [] for family in tags}
    for trade in stats_from(archive)["trades"]:
        codes = set((trade["enter_tag"] or "").split())
        family = next((name for name, group in tags.items() if codes and codes <= group), None)
        if trade["is_short"] or family is None:
            continue
        clock = pd.Timestamp(trade["open_timestamp"], unit="ms", tz="UTC")
        prior = account["curve"].marked_equity.get(clock - FIVE_MINUTES)
        result = quantify(trade)
        result["first_fill"] = clock.isoformat()
        result["lifetime_multiple_of_first_margin"] = result["lifetime_entry_margin_usdt"] / result["first_margin_usdt"]
        result["net_fraction_prior_nav"] = result["realized_pnl_usdt"] / float(prior) if (
            prior is not None and prior > 0 and result["exit_reason"] != "force_exit"
        ) else None
        if result["exit_reason"] == "force_exit":
            result.pop("realized_pnl_usdt")
        rows[family].append(result)
    results = {}
    for family, cases in rows.items():
        exact = [case for case in cases if set(case["strat_tag"].split()) == {EXACT[family]}]
        results[family] = {"pure_family": summarize(cases), "pure_named_tag": summarize(exact), "rows": cases}
    return {"registered_plan": plan_ref, "account_sources": account["refs"], "families": results}


def main():
    policy = receipt(HERE / "long_adjustment_margin_protocol.json")
    source = receipt(Path(__file__))
    full_ref = receipt(HERE / "recent_two_year_15bp_12_verified.json")
    primary = read(checked(full_ref))["account"]
    registration = read(checked(primary["registered_plan"]))
    pinned = registration["candidates"][0]["source_files"]["NostalgiaForInfinityX8.py"]
    tags = mode_tags(checked(pinned))
    full = inspect(read(checked(primary["proof"])), tags, pinned)
    reset = {name: {"account_summary": ref, **inspect(a, tags, pinned)} for name, ref, a, _ in reports(8)}
    drawdown_ref = receipt(HERE / "recent_two_year_drawdown_audit.json")
    window = read(checked(drawdown_ref))["continuous_peak_to_trough"]
    trough_ms = pd.Timestamp(window["trough"]).value // 1_000_000
    key_map = {(row["pair"], row["first_fill"]): row for family in full["families"].values() for row in family["rows"]}
    major = []
    for row in window["campaigns"][:3]:
        match = key_map[row["pair"], row["opening_fill"]]
        first_hit = match["first_crossed_ms"]
        major.append({**row, "margin_path": match, "cap_crossed_before_trough": first_hit is not None and first_hit < trough_ms})
    cases = [full, *reset.values()]
    winners = sum(family["pure_family"]["concurrent_above_2x_1pct_winners"]
                  for case in cases for family in case["families"].values())
    hits = sum(row["cap_crossed_before_trough"] for row in major)
    write_once(HERE / "long_adjustment_margin_result.json", {
        "policy": policy, "source": source, "native_strategy": pinned, "capital_basis": full_ref, "drawdown_source": drawdown_ref,
        "margin_reconstruction_source": receipt(Path(quantify.__code__.co_filename)),
        "source_mode_tags": {name: sorted(values) for name, values in tags.items()},
        "continuous_15bp": full, "reset_accounts_8bp": reset, "major_drawdown_campaigns": major,
        "major_campaigns_reached_before_trough": hits, "large_natural_winners_also_reached": winners,
        "advance": hits > 0 and winners == 0, "new_native_accounts": 0, "orders_enabled": False,
    })
    print("major campaigns reached", hits, "large natural winners also reached", winners, flush=True)


if __name__ == "__main__":
    main()
