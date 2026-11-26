"""Reconcile the completed twelve-slot wallets across the two frozen cost scenarios."""

from math import fsum, isclose
from pathlib import Path

import pandas as pd

from research.crypto.head_shoulders_financial_native_20261117_v3 import independent_account_audit_v2
from research.crypto.nfi40_boost_profit_tail_20261125_v1.holdout_accounts import invariant_config
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .inputs import HERE
from .native_trade_path_review import books, timestamp
from .recent_two_year_attribution import MARK_TOLERANCE


def config_without_fee(cfg):
    normalized = invariant_config(cfg)
    normalized.pop("fee", None)
    if "original_config" in normalized:
        normalized["original_config"] = config_without_fee(normalized["original_config"])
    return normalized


def parts(trade):
    fields = independent_account_audit_v2.cash_from_orders(trade)
    if float(fields["error"]) > MARK_TOLERANCE:
        raise ValueError(f"Observed filled cash does not reconcile for {trade['pair']}")
    return {name: float(fields[name]) for name in (
        "entry_cash", "exit_cash", "price_pnl", "commission", "funding", "net_pnl",
    )}


def totals(cash, keys):
    rows = [cash[key] for key in sorted(keys)]
    return {
        "count": len(rows),
        "net_usdt": fsum(row["net_pnl"] for row in rows),
        "gross_price_pnl_usdt": fsum(row["price_pnl"] for row in rows),
        "commissions_usdt": fsum(row["commission"] for row in rows),
        "funding_usdt": fsum(row["funding"] for row in rows),
        "executed_notional_usdt": fsum(row["entry_cash"] + row["exit_cash"] for row in rows),
    }


def compare_natural(accounts):
    left, left_orders = accounts[15]
    right, right_orders = accounts[8]
    high_cost, low_cost = left["trades"], right["trades"]
    common = high_cost.keys() & low_cost.keys()
    same = {key for key in common
            if high_cost[key]["fill_signature"] == low_cost[key]["fill_signature"]}
    scopes = {
        "same_fill_times_and_prices": (same, same),
        "matched_first_fills_with_different_paths": (common - same, common - same),
        "unmatched_first_fills": (high_cost.keys() - common, low_cost.keys() - common),
    }
    amounts = {
        15: {key: parts(left_orders[key]) for key in high_cost},
        8: {key: parts(right_orders[key]) for key in low_cost},
    }
    groups = {}
    for name, (older, newer) in scopes.items():
        high = totals(amounts[15], older)
        low = totals(amounts[8], newer)
        groups[name] = {"15bp": high, "8bp": low, "8_minus_15": {
            field: low[field] - high[field] for field in high
        }}
    for fee, wallet in ((15, left), (8, right)):
        natural = totals(amounts[fee], wallet["trades"])
        realized = wallet["curve"].closed_realized_pnl.iloc[-1]
        if not isclose(natural["net_usdt"], realized, abs_tol=MARK_TOLERANCE, rel_tol=0):
            raise ValueError(f"The {fee} bp cash attribution does not match the native ledger")
    return groups


def find_near(wallet, native):
    key = ("NEAR/USDT:USDT", True, pd.Timestamp("2026-08-14T07:45:00Z"))
    if key not in wallet["trades"]:
        return None
    row = wallet["trades"][key]
    trade = native[key]
    return {
        "first_fill": key[2].isoformat(),
        "close": timestamp(trade["close_timestamp"]).isoformat(),
        "tag": trade["enter_tag"].strip(),
        "net_usdt": row["profit"],
        "prior_nav_usdt": row["equity_before_entry"],
        "additional_filled_entries": row["entry_fills"] - 1,
        "exit_reason": trade["exit_reason"],
        "filled_cash": parts(trade),
    }


def main():
    sources = {fee: receipt(HERE / f"recent_two_year_{fee}bp_12_verified.json") for fee in (15, 8)}
    originals = {fee: read(checked(source)) for fee, source in sources.items()}
    accounts = {fee: row["account"] for fee, row in originals.items()}
    configs = {fee: read(checked(row["effective_config"])) for fee, row in accounts.items()}
    plans = {fee: read(checked(row["registered_plan"])) for fee, row in accounts.items()}
    checks = {
        "matching_input_preflight": originals[15]["preflight"] == originals[8]["preflight"],
        "matching_config_excluding_cost": (
            config_without_fee(configs[15]) == config_without_fee(configs[8])
        ),
        "matching_strategy_sources": (
            plans[15]["candidates"][0]["source_files"] == plans[8]["candidates"][0]["source_files"]
        ),
        "matching_market_input": plans[15]["data_receipts"] == plans[8]["data_receipts"],
    }
    for fee, effective in configs.items():
        if (effective["max_open_trades"], effective["fee"]) != (12, fee / 10000):
            raise ValueError("Wrong native slot limit or fee setting")
    loaded = {fee: books(read(checked(row["proof"]))) for fee, row in accounts.items()}
    left, right = (loaded[fee][0] for fee in (15, 8))
    checks["matching_marks"] = left["curve"].index.equals(right["curve"].index)
    if not all(checks.values()):
        raise ValueError(f"Account comparison mismatch: {checks}")
    natural = compare_natural(loaded)
    common = natural["same_fill_times_and_prices"]
    basis = (common["15bp"]["executed_notional_usdt"] + common["8bp"]["executed_notional_usdt"]) / 2
    mechanical = basis * (15 - 8) / 10000
    changes = [row["8_minus_15"] for row in natural.values()]
    marking = right["execution_to_mark_delta_usdt"] - left["execution_to_mark_delta_usdt"]
    net_components = {
        "natural_price_pnl": fsum(row["gross_price_pnl_usdt"] for row in changes),
        "natural_commissions_saved": -fsum(row["commissions_usdt"] for row in changes),
        "natural_funding": fsum(row["funding_usdt"] for row in changes),
        "terminal_positions": right["forced_position_pnl_usdt"] - left["forced_position_pnl_usdt"],
        "execution_to_mark": marking,
    }
    delta = float(right["curve"].marked_equity.iloc[-1] - left["curve"].marked_equity.iloc[-1])
    if not isclose(fsum(net_components.values()), delta, abs_tol=MARK_TOLERANCE, rel_tol=0):
        raise ValueError("Cost components do not sum to the marked terminal NAV difference")
    write_once(HERE / "recent_two_year_cost_attribution.json", {
        "source": receipt(Path(__file__)),
        "verified_accounts": {str(fee): source for fee, source in sources.items()},
        "same_contract_checks": checks, "natural_campaign_groups": natural,
        "8_minus_15_terminal_nav_usdt": delta, "pnl_components_usdt": net_components,
        "matching_path_fee_rate_effect_at_average_notional_usdt": mechanical,
        "matching_path_fee_notional_effect_usdt": (
            -common["8_minus_15"]["commissions_usdt"] - mechanical
        ),
        "near_562_by_cost": {str(fee): find_near(*book) for fee, book in loaded.items()},
        "scope": (
            "Observed native accounts, both uninterrupted, amounts and marks from each actual "
            "replay. The identical-path fee-rate component is symmetric at the average executed "
            "notional. All remaining sizes and order paths respond to the original wallet."
        ),
        "orders_enabled": False,
    })
    print(net_components)


if __name__ == "__main__":
    main()
