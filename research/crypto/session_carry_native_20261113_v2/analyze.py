"""Audit native carry ownership, flips, cash/quantity and complete wallet sums."""

import json
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.band_trend_native_20261113_v1.analyze import combine, metrics, read_native
from research.crypto.session_carry_native_20261113_v2.native_runner import verify
from research.crypto.session_reversal_native_20261113_v1.analyze import funded_intents
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    audit_trades,
    save,
    sha,
)


HERE = Path(__file__).resolve().parent
OLD = HERE.parent / "session_reversal_native_20261113_v1"


def require(value, message):
    if not value:
        raise ValueError(message)


def actual_account(path, strategy, cost, plan):
    receipt = json.loads((path / "process_receipt.json").read_text())
    require(
        receipt["returncode"] == 0 and receipt["result_sha256"] == sha(path / "result.json"),
        "Incomplete/altered worker",
    )
    stats, archive, config = read_native(path, strategy)
    require(stats["dry_run_wallet"] == 500 and config["fee"] == cost / 10000, "Archived cash/fee")
    require(
        stats["max_open_trades"] == 1
        and stats["timeframe"] == "5m"
        and stats["trading_mode"] == "futures",
        "Archived contract",
    )
    require(
        all(t["leverage"] == 1 and len(t["orders"]) == 2 for t in stats["trades"]),
        "Leverage/quantity adjustment drift",
    )
    audit = audit_trades(stats)
    frame = pd.read_parquet(path / "hourly_equity.parquet")
    nav = frame.set_index("date").hourly_mark_equity.loc[plan["start"] : plan["comparison_end"]]
    grid = pd.date_range(plan["start"], plan["comparison_end"], freq="h").as_unit("ns")
    require(nav.index.as_unit("ns").equals(grid), "Missing/duplicate full NAV clock")
    nav.index = nav.index.as_unit("ns")
    require(nav.iloc[0] == 500, "Initial wallet")
    full = metrics(nav)
    result = json.loads((path / "result.json").read_text())
    require(abs(full["net_return"] - result["periods"][0]["net_return"]) < 1e-12, "Return audit")
    require(
        abs(full["maximum_hourly_mark_drawdown"] - result["periods"][0]["hourly_mark_drawdown"])
        < 1e-12,
        "Drawdown audit",
    )
    return stats, archive, nav, audit


def carry_audit(candidate, stats, plan, path, nav):
    expected = pd.read_parquet(Path(plan["output_root"]) / f"preflight_{candidate['name']}.parquet")
    snapshot = pd.read_parquet(path / "native_indicators.parquet")
    fields = [
        "next_session_boundary",
        "next_session_known",
        "next_session_lag_return",
        "next_session_target",
    ]
    snapshot = snapshot.loc[snapshot.date.ge(pd.Timestamp(plan["start"]))].set_index("date")
    reference = expected.set_index("date").loc[snapshot.index, fields]
    snapshot.index = snapshot.index.as_unit("ns")
    reference.index = reference.index.as_unit("ns")
    pd.testing.assert_frame_equal(snapshot[fields], reference, check_dtype=False, check_exact=True)
    events = expected.loc[expected.next_session_boundary].copy()
    events["execution_time"] = events.date + pd.Timedelta(minutes=5)
    events = events.loc[
        events.execution_time.ge(pd.Timestamp(plan["start"]))
        & events.execution_time.lt(pd.Timestamp(plan["end"]))
    ]
    trades = sorted(stats["trades"], key=lambda t: t["open_timestamp"])
    opening = np.array([t["open_timestamp"] for t in trades], dtype=np.int64)
    closing = np.array([t["close_timestamp"] for t in trades], dtype=np.int64)
    require(
        np.all(np.diff(opening) > 0) and np.all(np.diff(closing) >= 0), "Funded lifecycle order"
    )
    require(np.all(closing[:-1] <= opening[1:]), "Overlapping funded positions")
    cumulative = np.cumsum([t["profit_abs"] for t in trades])
    opens = {t["open_timestamp"]: t for t in trades}
    pair = candidate["pairs"][0]
    sym = pair.split("/")[0]
    market = json.loads(Path(plan["market_snapshot"]["path"]).read_text())["markets"][pair]
    step = Decimal(str(market["precision"]["amount"]))
    prices = pd.read_feather(
        Path(plan["datadir"]) / f"futures/{sym}_USDT_USDT-5m-futures.feather"
    ).set_index("date")
    delayed = candidate["strategy"] == "ClockMatchedCarrySource"
    seen = set()
    carried, reversals, rejected = 0, 0, []
    targets = {}
    for row in events.itertuples():
        clock = row.execution_time
        timestamp = clock.value // 1_000_000
        target = int(row.next_session_target)
        targets[timestamp] = target
        before = int(np.searchsorted(opening, timestamp, side="left")) - 1
        old = trades[before] if before >= 0 and closing[before] >= timestamp else None
        old_direction = (-1 if old["is_short"] else 1) if old else 0
        if old and old_direction == target:
            require(old["close_timestamp"] > timestamp, "Same-direction position was closed")
            carried += 1
            continue
        if old:
            require(
                old["close_timestamp"] == timestamp
                and old["exit_reason"] == "session_direction_change",
                "Opposite direction not closed at boundary",
            )
            reversals += int(target != 0)
        if target == 0:
            continue
        intended = timestamp + (300_000 if delayed else 0)
        filled = opens.get(intended)
        closed_count = int(np.searchsorted(closing, intended, side="right"))
        cash = 500 + (float(cumulative[closed_count - 1]) if closed_count else 0.0)
        available = cash * 0.99
        rate = float(prices.loc[pd.Timestamp(intended, unit="ms", tz="UTC"), "open"])
        minimum = max(
            market["limits"]["cost"]["min"] * 1.05 / 0.95,
            market["limits"]["amount"]["min"] * rate * 1.05,
        )
        quantity = (Decimal(str(available)) / Decimal(str(rate)) / step).to_integral_value(
            rounding=ROUND_DOWN
        ) * step
        if filled is None:
            require(available < minimum or quantity == 0, "Unexplained unfunded carry intent")
            rejected.append(
                {
                    "time": str(pd.Timestamp(intended, unit="ms", tz="UTC")),
                    "actual_closed_cash": cash,
                    "investable": available,
                    "minimum_stake": minimum,
                }
            )
            continue
        require(filled["is_short"] == (target == -1), "Actual funded direction differs")
        require(
            abs(filled["open_rate"] - rate) < 1e-8
            and abs(filled["amount"] - float(quantity)) < 1e-9,
            "Cash/lot/open-price reconstruction differs",
        )
        seen.add(intended)
    require(seen == set(opens), "Native trade not owned by frozen source intent")
    for trade in trades:
        if trade["exit_reason"] != "force_exit":
            require(
                trade["exit_reason"] == "session_direction_change", "Unclassified economic exit"
            )
            require(trade["close_timestamp"] in targets, "Exit outside boundary")
            require(
                targets[trade["close_timestamp"]] != (-1 if trade["is_short"] else 1),
                "Exit despite unchanged source direction",
            )

    # Reconstruct actual marked gross separately from the observer's futures
    # OPEN proxy gross. The existing hourly NAV supplies the audited denominator.
    marks = (
        pd.read_feather(Path(plan["datadir"]) / f"futures/{sym}_USDT_USDT-1h-mark.feather")
        .set_index("date")
        .open
    )
    mark_gross = np.zeros(len(nav), dtype=float)
    times = nav.index.as_unit("ms").asi8
    for trade in trades:
        active = (times > trade["open_timestamp"]) & (times <= trade["close_timestamp"])
        selected = nav.index[active]
        mark_gross[active] = marks.reindex(selected).to_numpy() * trade["amount"]
    require(np.isfinite(mark_gross).all(), "Missing actual held mark exposure")
    exposure = mark_gross / nav.to_numpy()
    return {
        "passed": True,
        "source_boundary_states": len(events),
        "actual_indicator_rows": len(snapshot),
        "same_direction_boundaries_carried": carried,
        "funded_direction_changes": reversals,
        "accepted_new_positions": len(seen),
        "unfunded_intents": len(rejected),
        "unfunded": rejected,
        "actual_quantity_fixed_while_held": True,
        "maximum_hourly_mark_gross_equity_ratio": float(exposure.max()),
        "hourly_fraction_gross_above_one": float(np.mean(exposure > 1)),
        "scope": "Reconciles actual native trades/cash; no external strategy account simulated",
    }


def main():
    plan = json.loads((HERE / "plan.json").read_text())
    require(
        json.loads((HERE / "execution_complete.json").read_text())["accounts"] == 16,
        "Incomplete matrix",
    )
    old_plan = json.loads((OLD / "plan.json").read_text())
    old_out = Path(old_plan["output_root"])
    out = Path(plan["output_root"])
    navs, rows, parity, evidence = {}, [], [], []
    for candidate in plan["candidates"]:
        for cost in candidate["costs"]:
            verify(plan, candidate)
            name = f"{candidate['name']}_{cost}bps"
            path = out / name
            stats, archive, nav, cash = actual_account(path, candidate["strategy"], cost, plan)
            save(path / "independent_archive_cash_recheck.json", cash)
            if candidate["role"] == "default_off_parity":
                old_stats, old_archive, old_nav, _ = actual_account(
                    old_out / name, candidate["strategy"], cost, plan
                )
                require(stats["trades"] == old_stats["trades"], "Default-off actual trades changed")
                require(
                    np.array_equal(nav.to_numpy(), old_nav.to_numpy())
                    and nav.index.equals(old_nav.index),
                    "Default-off full NAV changed",
                )
                audit = funded_intents(candidate, stats, plan, path)
                parity.append(
                    {
                        "account": name,
                        "trades": len(stats["trades"]),
                        "hourly_points": len(nav),
                        "exact_trades_and_nav": True,
                        "original_archive_sha256": sha(old_archive),
                    }
                )
            else:
                audit = carry_audit(candidate, stats, plan, path, nav)
            save(path / "policy_reconciliation.json", audit)
            navs[name] = nav
            rows.append(
                {
                    "account": candidate["name"],
                    "role": candidate["role"],
                    "cost_bps_per_side": cost,
                    **metrics(nav),
                    "native_trades": len(stats["trades"]),
                    "natural_closes": sum(
                        t["exit_reason"] != "force_exit" for t in stats["trades"]
                    ),
                    "forced_closes": sum(t["exit_reason"] == "force_exit" for t in stats["trades"]),
                    "unfunded_intents": audit["unfunded_intents"],
                    "same_direction_boundaries_carried": audit.get(
                        "same_direction_boundaries_carried"
                    ),
                    "maximum_mark_gross_equity_ratio": audit.get(
                        "maximum_hourly_mark_gross_equity_ratio"
                    ),
                    "native_terminal_return_separate": stats["profit_total"],
                    "native_funding_usdt": sum(t["funding_fees"] for t in stats["trades"]),
                }
            )
            evidence.append(
                {
                    "account": name,
                    "archive": str(archive),
                    "sha256": sha(archive),
                    "hourly_points": len(nav),
                    "cash_and_policy_passed": True,
                }
            )
    # Existing source and passive native accounts are comparison evidence,
    # explicitly reused rather than counted as newly run accounts.
    reused = []
    for cost in [15, 8]:
        for strategy in ["SourceSessions", "PassiveLong"]:
            for sym in ["BTC", "ETH"]:
                name = f"{strategy}_{sym}_{cost}bps"
                stats, archive, nav, _ = actual_account(old_out / name, strategy, cost, plan)
                navs[f"Old_{name}"] = nav
                reused.append(
                    {
                        "account": name,
                        "archive_sha256": sha(archive),
                        "trades": len(stats["trades"]),
                        "hourly_points": len(nav),
                    }
                )
    combinations = []
    for cost in [15, 8]:
        for policy in [
            "BoundaryCarrySource",
            "ClockMatchedCarrySource",
            "BoundaryCarryLong",
            "Old_SourceSessions",
            "Old_PassiveLong",
        ]:
            left = navs[f"{policy}_BTC_{cost}bps"]
            right = navs[f"{policy}_ETH_{cost}bps"]
            nav = combine(left, right)
            nav.to_frame("actual_native_wallet_sum").to_parquet(
                HERE / f"{policy}_{cost}bps_combined.parquet"
            )
            periods = [
                {"name": p["name"], **metrics(nav.loc[p["start"] : p["end"]])}
                for p in plan["periods"]
            ]
            corr = (
                left.resample("1D")
                .last()
                .pct_change()
                .corr(right.resample("1D").last().pct_change())
            )
            combinations.append(
                {
                    "policy": policy,
                    "cost_bps_per_side": cost,
                    "periods": periods,
                    "daily_leg_correlation": float(corr),
                    "actual_wallets": [500, 500],
                    "transfers_or_rebalance": False,
                }
            )
    gates = []
    for cost in [15, 8]:
        current = {
            r["policy"]: r["periods"] for r in combinations if r["cost_bps_per_side"] == cost
        }
        primary = current["BoundaryCarrySource"]
        accepted = all(
            p["net_return"] > 0 and p["maximum_hourly_mark_drawdown"] <= 0.5 for p in primary
        )
        accepted &= all(
            primary[0]["net_return"] > current[control][0]["net_return"]
            for control in ["BoundaryCarryLong", "Old_PassiveLong"]
        )
        gates.append({"cost_bps_per_side": cost, "passed": bool(accepted)})
    save(HERE / "comparison.json", {"accounts": rows, "combinations": combinations, "gates": gates})
    pd.DataFrame(rows).to_csv(HERE / "comparison.csv", index=False)
    save(HERE / "default_off_parity.json", {"passed": len(parity) == 4, "records": parity})
    save(
        HERE / "verification.json",
        {
            "passed_within_scope": True,
            "actual_new_native_accounts": len(rows),
            "new_policy_accounts": 12,
            "duplicate_technical_parity_accounts": 4,
            "new_hourly_points": sum(e["hourly_points"] for e in evidence),
            "evidence": evidence,
            "reused_prior_accounts": reused,
            "source_boundary_checks": sum(
                r["source_boundary_checks"]
                for r in json.loads((HERE / "preflight.json").read_text())["records"]
            ),
            "numeric_gates": gates,
            "independent_oos_or_live_qualified": False,
        },
    )
    passed = all(g["passed"] for g in gates)
    save(
        HERE / "status.json",
        {
            "status": "COMPLETE_NATIVE_CARRY_NUMERIC_PASS"
            if passed
            else "COMPLETE_NATIVE_CARRY_REJECTED",
            "native_accounts": len(rows),
            "numeric_pass": passed,
            "accepted_independent_new_alphas": 0,
            "goal_achieved": False,
            "goal_status": "active",
            "orders_enabled": False,
            "services_changed": False,
        },
    )
    print(
        json.dumps(
            {
                "accounts": len(rows),
                "parity_accounts_exact": len(parity),
                "gates": gates,
                "combinations": [
                    {"policy": r["policy"], "cost": r["cost_bps_per_side"], **r["periods"][0]}
                    for r in combinations
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
