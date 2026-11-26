"""Trace the existing short-rebuy margin budget on completed native accounts."""

import argparse
import ast
from decimal import Decimal
from math import fsum, isclose
from pathlib import Path

import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .inputs import HERE
from .panic_entry_audit import select_annual, select_recent
from .recent_two_year_attribution import ledger, reports


DISCOVERY_START = pd.Timestamp("2026-01-01T00:00:00Z")
DISCOVERY_END = pd.Timestamp("2026-11-09T05:00:00Z")
CAP = HERE.parent / "nfi80_online_rebuy_cap_20261125_v1"


def bind(reduced_cost):
    refs = {
        "protocol": receipt(HERE / "short_rebuy_transfer_protocol.json"),
        "script": receipt(Path(__file__)),
        "frozen_cap": receipt(CAP / "FlatBoostShortRebuyCap.py"),
        "frozen_cap_protocol": receipt(CAP / "protocol.json"),
        "original_comparison": receipt(CAP / "cost_path_comparison.json"),
        "original_reserve_admission": receipt(HERE / "short_rebuy_cap_reserve_admission.json"),
        "annual_15bp": receipt(HERE / "annual_2024_boost14_15bp_verified.json"),
        "continuous_15bp": receipt(HERE / "recent_two_year_15bp_comparison.json"),
        "completed_reduced_cost": {name: reference for name, reference, _, _ in reduced_cost},
        "longer_window_cap_paths_evaluated_at_freeze": False,
        "orders_enabled": False,
    }
    return write_once(HERE / "short_rebuy_transfer_freeze.json", refs), refs


def source_tags(path):
    cls, = (node for node in ast.parse(path.read_text()).body
            if isinstance(node, ast.ClassDef) and node.name == "NostalgiaForInfinityX8")
    tags, = (node.value for node in cls.body if isinstance(node, ast.Assign)
             and any(isinstance(target, ast.Name) and target.id == "short_rebuy_mode_tags"
                     for target in node.targets))
    return set(ast.literal_eval(tags))


def at_millis(value):
    return pd.Timestamp(value, unit="ms", tz="UTC")


def assess_campaign(trade, natural):
    entries = [order for order in trade["orders"] if order["ft_is_entry"]]
    leverage = Decimal(str(trade["leverage"]))
    margins = [
        float(Decimal(str(order["amount"])) * Decimal(str(order["safe_price"])) / leverage)
        for order in entries
    ]
    total = fsum(margins)
    if not isclose(total, trade["max_stake_amount"], abs_tol=1e-5, rel_tol=0):
        raise ValueError("Filled margin does not reconcile to the native campaign's max stake")
    limit = 2 * margins[0] + 1e-9
    crossed = next((slot for slot in range(1, len(entries))
                    if fsum(margins[:slot + 1]) > limit), None)
    if crossed is None:
        return None
    opening = at_millis(entries[0]["order_filled_timestamp"])
    prior = natural["equity_before_entry"] if natural is not None else None
    net = natural["profit"] if natural is not None else None
    return {
        "pair": trade["pair"],
        "tag": trade["enter_tag"].strip(),
        "first_fill": opening.isoformat(),
        "first_filled_budget_exceedance": at_millis(
            entries[crossed]["order_filled_timestamp"]
        ).isoformat(),
        "first_crossing_addition_number": crossed,
        "addition_count": len(entries) - 1,
        "first_margin_usdt": margins[0],
        "lifetime_margin_usdt": total,
        "lifetime_margin_over_first": total / margins[0],
        "natural_close_net_usdt": net,
        "natural_close_net_to_prior_nav": net / prior if prior and prior > 0 else None,
        "outside_discovery_window": bool(opening < DISCOVERY_START or opening > DISCOVERY_END),
    }


def account(account_proof, pinned_source, tags):
    original = ledger(account_proof)
    archive = checked(original["refs"][".zip"])
    plan_ref = receipt(archive.parents[1] / "registered_plan.json")
    candidate, = read(checked(plan_ref))["candidates"]
    strategy = candidate["source_files"]["NostalgiaForInfinityX8.py"]
    if strategy["sha256"] != pinned_source["sha256"]:
        raise ValueError("The registered short-tag implementation differs")
    checked(strategy)
    crossed = []
    natural_count = terminal_count = 0
    for trade in stats_from(archive)["trades"]:
        modes = set((trade["enter_tag"] or "").split())
        if not trade["is_short"] or not modes or not modes <= tags:
            continue
        clock = at_millis(trade["open_timestamp"])
        terminal = trade["exit_reason"] == "force_exit"
        realized = None if terminal else original["trades"][(trade["pair"], True, clock)]
        terminal_count += terminal
        natural_count += not terminal
        event = assess_campaign(trade, realized)
        if event is not None:
            crossed.append(event)
    return {
        "registered_plan": plan_ref,
        "native_account_refs": original["refs"],
        "natural_short_rebuy_campaigns": natural_count,
        "terminal_short_rebuy_campaigns": terminal_count,
        "natural_beyond_2x_count": sum(
            row["natural_close_net_usdt"] is not None for row in crossed
        ),
        "crossed_campaigns": crossed,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args()
    reduced_cost = list(reports(8))
    freeze, refs = bind(reduced_cost)
    if args.freeze_only:
        print(freeze)
        return
    continuous = read(checked(refs["continuous_15bp"]))["accounts"]
    year_2024 = read(checked(refs["annual_15bp"]))["accounts"]
    plan = read(checked(continuous["14"]["registered_plan"]))
    source = plan["candidates"][0]["source_files"]["NostalgiaForInfinityX8.py"]
    tags = source_tags(checked(source))
    cohort = {
        "2024_15bp": {role: proof for role, (proof, _) in select_annual(year_2024).items()},
        "continuous_15bp": {role: proof for role, (proof, _) in select_recent(continuous).items()},
        **{f"{name}_8bp": {"12": control, "14": candidate}
           for name, _, control, candidate in reduced_cost},
    }
    cases = {
        period: {role: account(proof, source, tags) for role, proof in wallets.items()}
        for period, wallets in cohort.items()
    }
    candidate = [
        {"period": period, **trade}
        for period, rows in cases.items()
        for trade in rows["14"]["crossed_campaigns"]
        if trade["natural_close_net_to_prior_nav"] is not None
    ]
    losses = [trade for trade in candidate if trade["outside_discovery_window"]
              and trade["natural_close_net_to_prior_nav"] <= -.01]
    winners = [trade for trade in candidate if trade["natural_close_net_to_prior_nav"] >= .01]
    write_once(HERE / "short_rebuy_transfer_audit.json", {
        "freeze": freeze,
        "pinned_native_source": source,
        "short_rebuy_tags": sorted(tags),
        "cases": cases,
        "14_slot_losses_reached_outside_discovery": losses,
        "14_slot_large_winners_reached": winners,
        "admit_14_slot_calculation": bool(losses and not winners),
        "new_native_accounts": 0,
        "orders_enabled": False,
    })
    for period, rows in cases.items():
        print(period, {role: row["natural_beyond_2x_count"] for role, row in rows.items()})


if __name__ == "__main__":
    main()
