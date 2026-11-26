"""Run paired extended Freqtrade wallets and audit their 2024 start boundary."""

import argparse
import os
from pathlib import Path
from time import sleep

import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.holdout_accounts import invariant_config
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .continuous_november_run import PROGRESS, ROOT, ROOT_RUN, RUNS
from .continuous_run import launch, report_continuous
from .inputs import HERE
from .recent_two_year_attribution import attribution, ledger
from .recent_two_year_prefix_audit import fill_prefix, load_completed
from .short_rebuy_cap_2024 import FOLDER as ANNUAL_CAP, source_file


SLOTS = (12, 14)
END_ANNUAL = pd.Timestamp("2025-01-01T00:00:00Z")
ANNUAL = HERE / "annual_2024_boost14_15bp_verified.json"
GATE = HERE / "continuous_november_source_gate.json"


def registrations(fee):
    gate = read(GATE)
    plans = {}
    for slots in SLOTS:
        ref = gate["plans"][f"{slots}_{fee}bp"]
        plan = read(checked(ref))
        runner = receipt(checked(ref).with_name("runner.py"))
        assert runner["sha256"] == plan["runner_sha256"] and plan["data_receipts"] == gate["manifest"]
        assert plan["candidates"][0]["max_open_trades"] == slots
        assert plan["initial_equity_usdt"] == 1000 and plan["allowed_leverage"] == 3
        assert plan["orders_enabled"] is False
        plans[slots] = runner, ref
    return plans


def inspect_annual_bindings():
    gate_ref, annual_ref = receipt(GATE), receipt(ANNUAL)
    gate = read(checked(gate_ref))
    corpus = read(checked(gate["manifest"]))
    original = read(checked(annual_ref))
    new_schedule = read(checked(corpus["schedule"]))["days"]
    by_slot = {}
    for slots, (runner, plan_ref) in registrations(15).items():
        role = "fixed_boost_12" if slots == 12 else "boost_14"
        prior = original["accounts"][role]
        old_ref = source_file(prior["receipts"], "registered_plan.json")
        old, new = read(checked(old_ref)), read(checked(plan_ref))
        assert old["engine_commit"] == new["engine_commit"]
        a, b = (p["candidates"][0] for p in (old, new))
        assert a["strategy"] == b["strategy"]
        assert {name: ref["sha256"] for name, ref in a["source_files"].items()} == {
            name: ref["sha256"] for name, ref in b["source_files"].items()
        }
        old_config, new_config = (read(checked(c["config"])) for c in (a, b))
        exchanges = (c.pop("exchange") for c in (old_config, new_config))
        lhs, rhs = ({k: v for k, v in conf.items() if k != "pair_whitelist"} for conf in exchanges)
        assert lhs == rhs
        ignored = {"bot_name", "pairlists"}
        assert {k: v for k, v in old_config.items() if k not in ignored} == {
            k: v for k, v in new_config.items() if k not in ignored
        }
        old_schedule = read(checked(old["historical_schedule"]))["days"]
        assert len(old_schedule) == 366 and all(new_schedule[day] == row for day, row in old_schedule.items())
        old_markets, new_markets = (read(checked(x["market_snapshot"])) for x in (old, new))
        for field in ("markets", "leverage_tiers"):
            assert all(old_markets[field][pair] == new_markets[field][pair] for pair in old["pairs"])
        leaves = read(checked(old["data_receipts"]))
        for filename, fingerprint in leaves.items():
            first = corpus["files"][filename][0]
            assert first["source_year"] == "2024" and first["sha256"] == fingerprint
            assert first["start_inclusive"] is None and first["stop_exclusive"] == END_ANNUAL.isoformat()
        folder = Path(old_ref["path"]).parents[1]
        nav, trades, _ = load_completed(folder)
        assert len(nav) == 105408
        by_slot[str(slots)] = {
            "annual_plan": old_ref, "continuous_plan": plan_ref, "runner": runner,
            "identical_annual_pool_decisions": len(old_schedule), "identical_annual_file_fingerprints": len(leaves),
            "identical_market_and_tier_records": len(old["pairs"]),
            "annual_force_closed_positions": sum(t["exit_reason"] == "force_exit" for t in trades),
            "independent_annual_last_mark_usdt": float(nav.marked_equity.iloc[-1]),
        }
    return write_once(HERE / "continuous_november_2024_prefix_preflight.json", {
        "source": receipt(Path(__file__)), "continuous_gate": gate_ref,
        "annual_control": annual_ref, "by_slot": by_slot, "orders_enabled": False,
    })


def compare_2024_completed(slot, target, checks):
    baseline = Path(checked(checks["by_slot"][str(slot)]["annual_plan"])).parents[1]
    old_nav, old_trades, inputs = load_completed(baseline)
    new_nav, new_trades, outputs = load_completed(target)
    cutoff = END_ANNUAL - pd.Timedelta(minutes=10)
    left, right = old_nav.loc[:cutoff], new_nav.loc[:cutoff]
    old, new = (fill_prefix(trades, cutoff) for trades in (old_trades, new_trades))
    stamp = END_ANNUAL.value // 1_000_000
    continued = sorted((trade["pair"], trade["is_short"], trade["open_timestamp"])
                       for trade in new_trades if trade["open_timestamp"] < stamp <= trade["close_timestamp"])
    return {"cutoff": cutoff.isoformat(), "matched_mark_count": len(left), "same_marks": left.equals(right),
            "matched_fill_count": len(old), "same_fills": old == new, "positions_carried": continued,
            "annual_native": inputs, "continuous_native": outputs}


def run(fee, preflight):
    gate = read(GATE)
    witness = read(ROOT_RUN / "witness/matched.json")
    assert witness["exact_fills"] and witness["exact_full_marked_wallet"]
    original = read(checked(preflight))
    complete, wallets, references, configs = {}, {}, {}, {}
    for slots, registration in registrations(fee).items():
        folder = RUNS / f"full_{slots}_{fee}bp"
        launch(registration, folder, fee)
        report_continuous(ROOT, RUNS, PROGRESS)
        proof = receipt(folder / "account_reconciled.json")
        report = read(checked(proof))
        account = ledger(report)
        name = str(slots)
        assert len(account["curve"]) == gate["expected_five_minute_marks"]
        configs[name] = invariant_config(read(folder / "account/effective_config.json"))
        complete[name] = {"reconciled": proof, "completion": receipt(folder / "completion.json"),
                          "periods": report["periods"], "large_trades": report["natural_trade_size"]}
        wallets[name] = account
        if fee == 15:
            references[name] = compare_2024_completed(slots, folder, original)
        print("Verified complete", slots, fee, report["periods"]["continuous"], flush=True)
    assert configs["12"] == configs["14"]
    result = attribution(wallets["12"], wallets["14"])
    write_once(HERE / f"continuous_november_{fee}bp_comparison.json", {
        "preflight": preflight, "accounts": complete, "attribution": result,
        "independent_2024_prefix": references, "orders_enabled": False, "production_eligible": False,
    })


def wait_until_annual_ready(pid):
    print(f"Waiting for the annual cap queue {pid}", flush=True)
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        sleep(20)
    assert read(ANNUAL_CAP / "completion.json")["returncode"] == 0
    assert read(ANNUAL_CAP / "account_reconciled.json")["five_minute_nav_reconciliation"]["passed"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "run"))
    parser.add_argument("--cost", type=int, choices=(15, 8), default=15)
    parser.add_argument("--after-annual-pid", type=int)
    args = parser.parse_args()
    proof = inspect_annual_bindings()
    print("2024 native input bindings verified", proof["sha256"], flush=True)
    if args.mode == "run":
        if args.after_annual_pid is not None:
            wait_until_annual_ready(args.after_annual_pid)
        run(args.cost, proof)


if __name__ == "__main__":
    main()
