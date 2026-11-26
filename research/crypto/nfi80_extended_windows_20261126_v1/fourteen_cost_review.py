"""Reconcile fourteen-slot net cost sensitivity from the completed native wallets."""

from math import fsum, isclose
from pathlib import Path

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .inputs import HERE
from .native_trade_path_review import books
from .recent_two_year_attribution import MARK_TOLERANCE
from .recent_two_year_cost_attribution import compare_natural, config_without_fee, find_near
from .recent_two_year_trade_tails import sum_buckets


def side_totals(wallet):
    results = {}
    for side, short in (("long", False), ("short", True)):
        rows = [row for key, row in wallet["trades"].items() if key[1] == short]
        tails = sum_buckets(rows)
        results[side] = {
            "trades": len(rows),
            "net_realized_usdt": fsum(row["profit"] for row in rows),
            "large_wins": tails["large_win"]["natural_closes"],
            "large_losses": tails["large_loss"]["natural_closes"],
            "missing_prior_mark": tails["missing_prior_mark"]["natural_closes"],
        }
    return results


def main():
    references = {fee: receipt(HERE / f"recent_two_year_{fee}bp_14_verified.json") for fee in (15, 8)}
    reports = {fee: read(checked(ref)) for fee, ref in references.items()}
    accounts = {fee: report["account"] for fee, report in reports.items()}
    assert reports[15]["preflight"] == reports[8]["preflight"]
    configs, plans, native = {}, {}, {}
    for fee, account in accounts.items():
        cfg = read(checked(account["effective_config"]))
        assert (cfg["max_open_trades"], cfg["fee"]) == (14, fee / 10000)
        configs[fee] = config_without_fee(cfg)
        plan = read(checked(account["registered_plan"]))
        plans[fee] = plan["data_receipts"], plan["engine_manifest"], plan["candidates"][0]["source_files"]
        native[fee] = books(read(checked(account["proof"])))
    assert configs[15] == configs[8] and plans[15] == plans[8]
    high, low = (native[fee][0] for fee in (15, 8))
    assert high["curve"].index.equals(low["curve"].index)
    cash_groups = compare_natural(native)
    differences = [row["8_minus_15"] for row in cash_groups.values()]
    components = {
        "natural_price_pnl": fsum(row["gross_price_pnl_usdt"] for row in differences),
        "natural_commissions_saved": -fsum(row["commissions_usdt"] for row in differences),
        "natural_funding": fsum(row["funding_usdt"] for row in differences),
        "terminal_positions": low["forced_position_pnl_usdt"] - high["forced_position_pnl_usdt"],
        "execution_to_mark": low["execution_to_mark_delta_usdt"] - high["execution_to_mark_delta_usdt"],
    }
    value = float(low["curve"].marked_equity.iloc[-1] - high["curve"].marked_equity.iloc[-1])
    assert isclose(fsum(components.values()), value, abs_tol=MARK_TOLERANCE, rel_tol=0)
    directional = {str(fee): side_totals(wallet) for fee, (wallet, _) in native.items()}
    for fee, (wallet, _) in native.items():
        sides = directional[str(fee)]
        assert sum(item["trades"] for item in sides.values()) == len(wallet["trades"])
        closed = wallet["curve"].closed_realized_pnl.iloc[-1]
        assert isclose(fsum(item["net_realized_usdt"] for item in sides.values()), closed,
                       abs_tol=MARK_TOLERANCE, rel_tol=0)
    write_once(HERE / "recent_two_year_14_cost_attribution.json", {
        "source": receipt(Path(__file__)), "verified_accounts": {str(k): v for k, v in references.items()},
        "8_minus_15_terminal_nav_usdt": value, "cash_components": components,
        "natural_campaign_groups": cash_groups, "realized_by_side": directional,
        "near_562_by_cost": {str(fee): find_near(*book) for fee, book in native.items()},
        "orders_enabled": False,
    })
    print(value, components, directional, flush=True)


if __name__ == "__main__":
    main()
