"""Attribute large realized NFI wins to archived modes and initial stake."""

import ast
from math import isclose
from pathlib import Path
from statistics import median

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt, save


HERE = Path(__file__).resolve().parent


def source_tags(path):
    source = ast.parse(path.read_text())
    strategy, = [node for node in source.body if isinstance(node, ast.ClassDef)
                 and node.name == "NostalgiaForInfinityX8"]
    definition, = [node for node in strategy.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "long_top_coins_mode_tags"
                           for target in node.targets)]
    return set(ast.literal_eval(definition.value))


def position(trade, top_coins_tags):
    entries = [order for order in trade["orders"] if order["ft_is_entry"]]
    first = entries[0]
    margin = first["amount"] * first["safe_price"] / trade["leverage"]
    assert first["order_filled_timestamp"] == trade["open_timestamp"] and margin > 0
    tags = set(trade["enter_tag"].split())
    mode = "other_long"
    if trade["is_short"]:
        mode = "short"
    elif tags and tags <= top_coins_tags:
        mode = "top_coins_long"
    profit = trade["profit_abs"]
    one_entry_return = None
    if len(entries) == 1:
        assert isclose(margin, trade["max_stake_amount"], abs_tol=1e-6)
        one_entry_return = profit / margin
    return {
        "mode": mode, "pair": trade["pair"], "tags": sorted(tags), "open": trade["open_date"],
        "close": trade["close_date"], "exit_reason": trade["exit_reason"],
        "net_profit_usdt": profit, "initial_margin_usdt": margin, "entry_fills": len(entries),
        "one_entry_net_profit_on_margin": one_entry_return,
    }


def metrics(rows):
    profit = [row["net_profit_usdt"] for row in rows]
    ratios = [row["one_entry_net_profit_on_margin"] for row in rows if row["entry_fills"] == 1]
    return {
        "natural_closes": len(rows),
        "net_profit_at_least_25": sum(value >= 25 for value in profit),
        "net_profit_at_least_50": sum(value >= 50 for value in profit),
        "net_profit_usdt": sum(profit),
        "gross_gains_usdt": sum(value for value in profit if value > 0),
        "largest_winner_usdt": max((value for value in profit if value > 0), default=None),
        "largest_loss_usdt": min((value for value in profit if value < 0), default=None),
        "single_entry_trades": len(ratios),
        "single_entry_10pct_margin_win_count": sum(value >= .10 for value in ratios),
        "single_entry_10pct_margin_loss_count": sum(value <= -.10 for value in ratios),
        "single_entry_median_net_profit_on_margin": median(ratios) if ratios else None,
        "median_initial_margin_usdt": median([row["initial_margin_usdt"] for row in rows])
        if rows else None,
    }


def main():
    plan = read(HERE / "plan.json")
    upstream = plan["candidates"][0]["source_files"]["NostalgiaForInfinityX8.py"]
    tags = source_tags(checked(upstream))
    cases = {}
    for cost in (15, 8):
        report = HERE / f"comparison_{cost}bp.json"
        audited = read(report)
        assert audited["cost_bps_per_side"] == cost
        cases[str(cost)] = {}
        for role, label in (("control", "archived_fixed_boost_12"), ("candidate", "boost_14")):
            account = audited[role]
            assert account["five_minute_nav_reconciliation"]["passed"]
            export, = [row for row in account["sources"] if row["path"].endswith(".zip")]
            trades = stats_from(checked(export))["trades"]
            rows = [position(trade, tags) for trade in trades if trade["exit_reason"] != "force_exit"]
            assert len(rows) == audited["counts"][f"{role}_natural_closes"]
            cases[str(cost)][label] = {
                "report_receipt": receipt(report), "native_export_receipt": export,
                "all": metrics(rows),
                "modes": {mode: metrics([row for row in rows if row["mode"] == mode])
                          for mode in ("top_coins_long", "other_long", "short")},
                "top_five": sorted(rows, key=lambda row: -row["net_profit_usdt"])[:5],
            }
    save(HERE / "winner_attribution.json", {
        "scope": "Retrospective attribution from reconciled accounts. Mode differences reflect different trades.",
        "archived_mode_source": upstream, "top_coins_long_only_tags": sorted(tags),
        "start": plan["start"], "end": plan["comparison_end"], "initial_equity_usdt": 1000,
        "sizing_diagnostic": "For single entry trades, divide native net realized profit by first-fill margin.",
        "by_cost_bps_per_side": cases, "new_native_accounts": 0, "orders_enabled": False,
    })


if __name__ == "__main__":
    main()
