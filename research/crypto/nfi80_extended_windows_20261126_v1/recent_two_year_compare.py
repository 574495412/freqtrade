"""Compare verified, uninterrupted 12- and 14-slot Freqtrade wallets."""

import argparse
from math import isclose, prod
from pathlib import Path

import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.holdout_accounts import invariant_config
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import COSTS, write_once
from .continuous_run import SLOTS
from .inputs import HERE
from .recent_two_year_attribution import MARK_TOLERANCE, attribution, ledger
from .recent_two_year_run import GATE, RUNS
from .recent_two_year_trade_tails import summarize as summarize_trade_tails


def completed_account(slots, cost, preflight):
    folder = RUNS / f"full_{slots}_{cost}bp"
    reconciliation = folder / "account_reconciled.json"
    if not reconciliation.exists():
        return None
    proof = receipt(reconciliation)
    completed = receipt(folder / "completion.json")
    assert read(checked(completed)) == {"returncode": 0, "output": str(folder / "account")}
    plan_receipt = preflight["plans"][f"{slots}_{cost}bp"]
    plan = read(checked(plan_receipt))
    assert read(folder / "account/registered_plan.json") == plan and plan["data_receipts"] == preflight["manifest"]
    assert plan["candidates"][0]["max_open_trades"] == slots and not plan["orders_enabled"]
    assert plan["initial_equity_usdt"] == 1000 and plan["allowed_leverage"] == 3
    summary = read(checked(proof))
    assert summary["pool"]["decision_days_checked"] == preflight["verified_captured_days"]
    wallet = ledger(summary)
    curve = wallet["curve"]
    assert len(curve) == preflight["expected_five_minute_marks"]
    config_receipt, = [item for item in summary["sources"] if Path(item["path"]).name == "effective_config.json"]
    config = read(checked(config_receipt))
    assert config["max_open_trades"] == slots and config["fee"] == cost / 10_000
    assert config["margin_mode"] == "isolated" and config["dry_run"]
    periods = summary["periods"]
    for window in plan["periods"]:
        sliced = curve.loc[window["start"]:window["end"], "marked_equity"]
        observed = periods[window["name"]]
        assert len(sliced) == observed["marks"]
        assert isclose(float(sliced.iloc[-1]), observed["ending_equity"], abs_tol=MARK_TOLERANCE, rel_tol=0)
        assert isclose(float(sliced.iloc[-1] / sliced.iloc[0] - 1), observed["net_return"], abs_tol=1e-12, rel_tol=0)
    change = prod(1 + periods[year]["net_return"] for year in ("2025", "2026")) - 1
    assert isclose(change, periods["continuous"]["net_return"], abs_tol=1e-12, rel_tol=0)
    seam = curve.loc[pd.Timestamp("2026-01-01", tz="UTC")]
    report = {"proof": proof, "execution": completed, "registered_plan": plan_receipt,
              "effective_config": config_receipt, "periods": periods, "large_trades": summary["natural_trade_size"],
              "five_minute_nav_reconciliation": summary["five_minute_nav_reconciliation"],
              "carry_at_2026_utc_midnight": {"marked_nav_usdt": float(seam.marked_equity),
                                             "open_trades": int(seam.open_trades)},
              "slots_reached": summary["max_observed_positions"], "liquidations": summary["liquidations"]}
    return report, wallet, invariant_config(config)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cost", type=int, choices=COSTS, default=15)
    args = parser.parse_args()
    preflight = receipt(GATE)
    source = receipt(Path(__file__))
    register = read(checked(preflight))
    reports, wallets, configs = {}, {}, {}
    for slots in SLOTS:
        completed = completed_account(slots, args.cost, register)
        if completed is None:
            print(f"{slots}: awaiting independent reconciliation", flush=True)
            continue
        report, wallets[slots], configs[slots] = completed
        reports[slots] = report
        write_once(HERE / f"recent_two_year_{args.cost}bp_{slots}_verified.json", {
            "source": source, "preflight": preflight, "cost_bps_per_side": args.cost, "account": report,
            "orders_enabled": False,
        })
        print(slots, report["periods"]["continuous"], report["carry_at_2026_utc_midnight"], flush=True)
    if len(reports) != len(SLOTS):
        return
    assert configs[12] == configs[14]
    proof = attribution(wallets[12], wallets[14])
    tails = summarize_trade_tails(wallets[12], wallets[14], proof)
    control, candidate = (reports[slots]["periods"]["continuous"] for slots in SLOTS)
    write_once(HERE / f"recent_two_year_{args.cost}bp_comparison.json", {
        "source": source, "preflight": preflight, "cost_bps_per_side": args.cost,
        "accounts": {str(slots): report for slots, report in reports.items()},
        "return_lift": candidate["net_return"] - control["net_return"],
        "drawdown_change": candidate["drawdown"] - control["drawdown"],
        "return_improved": candidate["net_return"] > control["net_return"],
        "drawdown_not_increased": candidate["drawdown"] <= control["drawdown"],
        "same_initial_cash_leverage_and_config_except_strategy_and_slots": True,
        "fill_and_nav_attribution": proof, "large_trade_attribution": tails,
        "orders_enabled": False, "production_eligible": False,
    })


if __name__ == "__main__":
    main()
