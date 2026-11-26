"""Reconcile the peak open margin of naturally closed NFI long-scalp campaigns."""

import ast
from decimal import Decimal
from pathlib import Path
from statistics import median

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt, save

from .inputs import HERE


D = Decimal
MODE = "long_scalp_mode_tags"
PARENT = HERE.parent / "nfi80_online_baseline_20261125_v1"
ANNUAL = HERE / "annual_verified_progress.json"


def archived_mode(path):
    program = ast.parse(path.read_text())
    cls, = [node for node in program.body if isinstance(node, ast.ClassDef)
            and node.name == "NostalgiaForInfinityX8"]
    tags, = [node.value for node in cls.body if isinstance(node, ast.Assign)
             and any(isinstance(target, ast.Name) and target.id == MODE for target in node.targets)]
    return set(ast.literal_eval(tags))


def quantify(trade):
    leverage = D(str(trade["leverage"]))
    open_quantity, open_basis, lifetime = D(0), D(0), D(0)
    nominal_first = None
    maximum = D(0)
    increases = 0
    over_limit = 0
    first_crossed = None
    for order in trade["orders"]:
        quantity = D(str(order["amount"]))
        price = D(str(order["safe_price"]))
        assert quantity > 0 and price > 0
        if order["ft_is_entry"]:
            amount = quantity * price
            open_basis += amount
            open_quantity += quantity
            lifetime += amount
            if nominal_first is None:
                nominal_first = amount
            else:
                increases += 1
                if open_basis > 2 * nominal_first + D("0.00000001"):
                    over_limit += 1
                    if first_crossed is None:
                        first_crossed = order["order_filled_timestamp"]
            maximum = max(maximum, open_basis)
        else:
            next_quantity = open_quantity - quantity
            assert open_quantity > 0
            # Native order exports are rounded. An overage near machine precision
            # is retained in a final residual check rather than as open exposure.
            assert next_quantity >= -max(open_quantity, D(1)) * D("0.00000001")
            open_basis *= max(next_quantity, D(0)) / open_quantity
            open_quantity = max(next_quantity, D(0))
    assert nominal_first is not None
    assert abs(lifetime / leverage - D(str(trade["max_stake_amount"]))) <= D("0.00001")
    return {
        "pair": trade["pair"], "open_date": trade["open_date"], "strat_tag": trade["enter_tag"].strip(),
        "first_margin_usdt": float(nominal_first / leverage),
        "peak_open_margin_usdt": float(maximum / leverage),
        "peak_multiple_of_first_margin": float(maximum / nominal_first),
        "lifetime_entry_margin_usdt": float(lifetime / leverage),
        "additional_fills": increases, "additions_ending_above_2x": over_limit,
        "first_crossed_ms": first_crossed, "realized_pnl_usdt": trade["profit_abs"],
        "exit_reason": trade["exit_reason"],
    }


def account(archive, tags):
    trades = stats_from(checked(archive))["trades"]
    definitions = []
    for trade in trades:
        codes = set(trade["enter_tag"].split())
        if not trade["is_short"] and codes and codes <= tags and trade["exit_reason"] != "force_exit":
            definitions.append(quantify(trade))
    adverse = [row for row in definitions if row["additions_ending_above_2x"]]
    others = [row for row in definitions if not row["additions_ending_above_2x"]]
    return {
        "native_archive": archive, "natural_scalp_closes": len(definitions),
        "natural_above_2x_closes": len(adverse), "positive_adds_above_2x": sum(x["additions_ending_above_2x"] for x in adverse),
        "above_2x_net_realized_usdt": sum(x["realized_pnl_usdt"] for x in adverse),
        "other_realized_usdt": sum(x["realized_pnl_usdt"] for x in others),
        "median_peak_multiple": median(row["peak_multiple_of_first_margin"] for row in definitions)
        if definitions else None,
        "rows": definitions,
    }


def zipped(items):
    archive, = [row for row in items if Path(row["path"]).suffix == ".zip"]
    return archive


def main():
    policy = receipt(HERE / "scalp_margin_diagnostic.json")
    seal = receipt(ANNUAL)
    performance = read(checked(seal))
    annual = performance["accounts_by_cost_bp"]["15"]
    candidate = read(PARENT / "plan.json")["candidates"][0]
    source = candidate["source_files"]["NostalgiaForInfinityX8.py"]
    tags = archived_mode(checked(source))
    existing = {name: account(zipped(row["receipts"]), tags) for name, row in annual.items()}
    forward, receipts = {}, {}
    for cost in (15, 8):
        path = PARENT / f"comparison_{cost}bp.json"
        receipts[str(cost)] = receipt(path)
        controls = read(checked(receipts[str(cost)]))
        assert controls["control"]["five_minute_nav_reconciliation"]["passed"]
        forward[str(cost)] = account(zipped(controls["control"]["sources"]), tags)
    save(HERE / "scalp_margin_result.json", {
        "rule": policy, "archive_mode_source": source, "source_defined_tags": sorted(tags),
        "annual_comparison": seal, "2026_archive_comparisons": receipts,
        "year_2025_15bp": existing, "year_2026_fixed12": forward,
        "new_wallets": 0, "orders_enabled": False,
    })
    print("long-scalp scope", {name: (row["natural_above_2x_closes"], row["above_2x_net_realized_usdt"])
                             for name, row in existing.items()}, flush=True)


if __name__ == "__main__":
    main()
