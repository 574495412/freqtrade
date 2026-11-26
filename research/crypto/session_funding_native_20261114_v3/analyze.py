"""Reconcile full native scenario accounts, event ownership and feedback."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.band_trend_native_20261113_v1.analyze import combine, metrics
from research.crypto.session_carry_native_20261113_v2.analyze import actual_account, carry_audit
from research.crypto.session_funding_native_20261114_v3.native_runner import verify
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha


HERE = Path(__file__).resolve().parent
OLD = HERE.parent / "session_carry_native_20261113_v2"


def require(value, reason):
    if not value:
        raise ValueError(reason)


def funding_audit(stats, mode, plan):
    pair = stats["pairlist"][0]
    sym = pair.split("/")[0]
    fund = pd.read_feather(
        Path(plan["datadir"]) / f"futures/{sym}_USDT_USDT-1h-funding_rate.feather"
    )
    fund = fund.loc[
        fund.date.ge(pd.Timestamp(plan["start"])) & fund.date.lt(pd.Timestamp(plan["end"]))
    ]
    marks = (
        pd.read_feather(Path(plan["datadir"]) / f"futures/{sym}_USDT_USDT-1h-mark.feather")
        .set_index("date")
        .open
    )
    times = pd.DatetimeIndex(fund.date).as_unit("ms").asi8
    unit = fund.funding_rate.to_numpy() * marks.reindex(pd.DatetimeIndex(fund.date)).to_numpy()
    require(np.isfinite(unit).all(), "Missing funding/mark input")
    counts = np.zeros(len(times), dtype=int)
    errors = []
    for trade in stats["trades"]:
        opened, closed = trade["open_timestamp"], trade["close_timestamp"]
        if mode == "inclusive":
            mask = (times >= opened) & (times <= closed)
        elif mode == "before_orders":
            mask = (times > opened) & (times <= closed)
        else:
            mask = (times >= opened) & (times < closed)
        expected = sum(float(x) * trade["amount"] for x in unit[mask])
        expected *= 1 if trade["is_short"] else -1
        errors.append(abs(expected - trade["funding_fees"]))
        counts += mask.astype(int)
    require(
        max(errors, default=0) < 1e-8,
        "Actual native funding differs from independent scalar lifecycle rule",
    )
    if mode != "inclusive":
        require(counts.max(initial=0) <= 1, "Event assigned to two funded lifecycles")
    return {
        "passed": True,
        "mode": mode,
        "full_input_events": len(times),
        "assigned_event_lifecycles": int(counts.sum()),
        "dual_owned_events": int((counts > 1).sum()),
        "maximum_per_trade_error_usdt": max(errors, default=0),
        "actual_account_settlement_certified": False,
    }


def main():
    plan = json.loads((HERE / "plan.json").read_text())
    require(
        json.loads((HERE / "execution_complete.json").read_text())["accounts"] == 20,
        "Incomplete native matrix",
    )
    old_plan = json.loads((OLD / "plan.json").read_text())
    old_out = Path(old_plan["output_root"])
    out = Path(plan["output_root"])
    rows, evidence, parity, feedback, navs = [], [], [], [], {}
    for candidate in plan["candidates"]:
        for cost in candidate["costs"]:
            verify(plan, candidate)
            name = f"{candidate['name']}_{cost}bps"
            path = out / name
            stats, archive, nav, cash = actual_account(path, candidate["strategy"], cost, plan)
            old_name = f"{candidate['original_candidate']}_{cost}bps"
            old_stats, _old_archive, old_nav, _ = actual_account(
                old_out / old_name, candidate["strategy"], cost, plan
            )
            policy_metadata = json.loads((path / "funding_policy.json").read_text())
            require(
                policy_metadata["mode"] == candidate["funding_ownership"]
                and policy_metadata["implementation"] == plan["funding_extension"],
                "Sealed sidecar policy metadata mismatch",
            )
            funding = funding_audit(stats, candidate["funding_ownership"], plan)
            policy = carry_audit(candidate, stats, plan, path, nav)
            extension = json.loads((path / "funding_extension_audit.json").read_text())
            require(
                extension["closing_context_empty"]
                and extension["policy"] == candidate["funding_ownership"],
                "Native funding callback state",
            )
            if candidate["role"] == "default_off_parity":
                require(
                    stats["trades"] == old_stats["trades"]
                    and np.array_equal(nav.to_numpy(), old_nav.to_numpy()),
                    "Default-off financial parity failed",
                )
                require(
                    extension["active_native_funding_calls"] == 0, "Default-off active extension"
                )
                parity.append(
                    {
                        "account": name,
                        "trades": len(stats["trades"]),
                        "hourly_points": len(nav),
                        "exact_native_trades_and_nav": True,
                    }
                )
            else:
                require(
                    extension["active_native_funding_calls"] > 0,
                    "No actual native funding extension execution",
                )
                shape = ["open_timestamp", "close_timestamp", "is_short", "open_rate", "close_rate"]
                equal_path = len(stats["trades"]) == len(old_stats["trades"]) and all(
                    all(t[k] == u[k] for k in shape)
                    for t, u in zip(stats["trades"], old_stats["trades"], strict=False)
                )
                quantity_changes = (
                    sum(
                        t["amount"] != u["amount"]
                        for t, u in zip(stats["trades"], old_stats["trades"], strict=False)
                    )
                    if equal_path
                    else None
                )
                delta = nav - old_nav
                feedback.append(
                    {
                        "account": name,
                        "timing_direction_price_path_same_as_inclusive": equal_path,
                        "actual_quantity_changes": quantity_changes,
                        "terminal_cash_delta_usdt": stats["final_balance"]
                        - old_stats["final_balance"],
                        "max_abs_hourly_nav_delta_usdt": float(delta.abs().max()),
                        "last_common_nav_delta_usdt": float(delta.iloc[-1]),
                        "native_quantities_and_wallet_replayed": True,
                    }
                )
            save(path / "independent_cash_audit.json", cash)
            save(path / "policy_reconciliation.json", policy)
            save(path / "funding_reconciliation.json", funding)
            navs[
                (
                    candidate["strategy"],
                    candidate["pairs"][0].split("/")[0],
                    candidate["funding_ownership"],
                    cost,
                )
            ] = nav
            rows.append(
                {
                    "account": candidate["name"],
                    "strategy": candidate["strategy"],
                    "ownership": candidate["funding_ownership"],
                    "cost_bps_per_side": cost,
                    **metrics(nav),
                    "native_trades": len(stats["trades"]),
                    "unfunded_intents": policy["unfunded_intents"],
                    "dual_owned_events": funding["dual_owned_events"],
                    "native_funding_usdt": sum(t["funding_fees"] for t in stats["trades"]),
                }
            )
            evidence.append(
                {
                    "account": name,
                    "native_archive": str(archive),
                    "sha256": sha(archive),
                    "hourly_points": len(nav),
                    "cash_policy_funding_passed": True,
                }
            )
    # Inclusive8bps references are existing accounts, not fresh processes.
    reused = []
    for strategy in ["BoundaryCarrySource", "BoundaryCarryLong"]:
        for sym in ["BTC", "ETH"]:
            name = f"{strategy}_{sym}_8bps"
            stats, archive, nav, _ = actual_account(old_out / name, strategy, 8, plan)
            navs[(strategy, sym, "inclusive", 8)] = nav
            reused.append(
                {"account": name, "archive_sha256": sha(archive), "hourly_points": len(nav)}
            )
    combinations, gates = [], []
    for mode in ["inclusive", "before_orders", "after_orders"]:
        for cost in [15, 8]:
            for strategy in ["BoundaryCarrySource", "BoundaryCarryLong"]:
                nav = combine(
                    navs[(strategy, "BTC", mode, cost)], navs[(strategy, "ETH", mode, cost)]
                )
                periods = [
                    {"name": p["name"], **metrics(nav.loc[p["start"] : p["end"]])}
                    for p in plan["periods"]
                ]
                nav.to_frame("actual_native_wallet_sum").to_parquet(
                    HERE / f"{strategy}_{mode}_{cost}bps_combined.parquet"
                )
                combinations.append(
                    {
                        "strategy": strategy,
                        "ownership": mode,
                        "cost_bps_per_side": cost,
                        "periods": periods,
                        "actual_wallets": [500, 500],
                        "transfers": False,
                    }
                )
            source = next(
                r
                for r in combinations
                if r["strategy"] == "BoundaryCarrySource"
                and r["ownership"] == mode
                and r["cost_bps_per_side"] == cost
            )
            long = next(
                r
                for r in combinations
                if r["strategy"] == "BoundaryCarryLong"
                and r["ownership"] == mode
                and r["cost_bps_per_side"] == cost
            )
            passed = all(
                p["net_return"] > 0 and p["maximum_hourly_mark_drawdown"] <= 0.5
                for p in source["periods"]
            )
            passed &= source["periods"][0]["net_return"] > long["periods"][0]["net_return"]
            # Previously required old passive reference remains a gate.
            passive = json.loads((OLD / "comparison.json").read_text())["combinations"]
            reference = next(
                r
                for r in passive
                if r["policy"] == "Old_PassiveLong" and r["cost_bps_per_side"] == cost
            )
            passed &= source["periods"][0]["net_return"] > reference["periods"][0]["net_return"]
            gates.append({"ownership": mode, "cost_bps_per_side": cost, "passed": bool(passed)})
    save(HERE / "comparison.json", {"accounts": rows, "combinations": combinations, "gates": gates})
    pd.DataFrame(rows).to_csv(HERE / "comparison.csv", index=False)
    save(HERE / "default_off_parity.json", {"passed": len(parity) == 4, "records": parity})
    save(
        HERE / "cash_feedback.json",
        {"full_native_replay_not_fixed_fill_correction": True, "records": feedback},
    )
    save(
        HERE / "verification.json",
        {
            "passed_within_scope": True,
            "actual_new_native_processes": len(rows),
            "scenario_accounts": 16,
            "technical_parity_accounts": 4,
            "new_hourly_points": sum(r["hourly_points"] for r in evidence),
            "evidence": evidence,
            "prior8bps_accounts_reused": reused,
            "no_dual_event_ownership_in_active_scenarios": all(
                r["dual_owned_events"] == 0 for r in rows if r["ownership"] != "inclusive"
            ),
            "independent_oos_or_private_settlement_certified": False,
            "gates": gates,
        },
    )
    accepted = all(g["passed"] for g in gates if g["ownership"] != "inclusive")
    save(
        HERE / "status.json",
        {
            "status": "COMPLETE_NATIVE_FUNDING_SCENARIOS_NUMERIC_PASS"
            if accepted
            else "COMPLETE_NATIVE_FUNDING_SCENARIOS_REJECTED",
            "new_native_accounts": len(rows),
            "accepted_independent_alphas": 0,
            "goal_achieved": False,
            "goal_status": "active",
            "orders_enabled": False,
            "services_changed": False,
        },
    )
    print(
        json.dumps(
            {
                "native_processes": len(rows),
                "default_off_exact": len(parity),
                "source_combinations": [
                    {"ownership": r["ownership"], "cost": r["cost_bps_per_side"], **r["periods"][0]}
                    for r in combinations
                    if r["strategy"] == "BoundaryCarrySource"
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
