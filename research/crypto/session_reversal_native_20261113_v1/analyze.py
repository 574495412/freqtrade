"""Inspect actual native archives, funded intents and full-clock sleeve sums."""

import json
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

import pandas as pd

from research.crypto.band_trend_native_20261113_v1.analyze import combine, metrics, read_native
from research.crypto.session_reversal_native_20261113_v1.native_runner import verify
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    audit_trades,
    save,
    sha,
)


HERE = Path(__file__).resolve().parent


def require(value, message):
    if not value:
        raise ValueError(message)


def funded_intents(candidate, stats, plan, path):
    """Reconcile intents to ACTUAL native trade cash, never simulate returns."""
    pair = candidate["pairs"][0]
    sym = pair.split("/")[0]
    root = Path(plan["output_root"])
    expected = pd.read_parquet(root / f"preflight_{candidate['name']}.parquet")
    original = pd.read_feather(
        Path(plan["datadir"]) / f"futures/{sym}_USDT_USDT-5m-futures.feather"
    ).set_index("date")
    actual_indicators = pd.read_parquet(path / "native_indicators.parquet")
    # The native loader has a shorter warmup than diagnostic full input.
    fields = ["lag_same_session_return", "session_boundary", "session_known", "session_target"]
    actual_indicators = actual_indicators.loc[
        actual_indicators.date.ge(pd.Timestamp(plan["start"]))
    ].set_index("date")
    reference = expected.set_index("date").loc[actual_indicators.index, fields]
    actual_indicators.index = actual_indicators.index.as_unit("ns")
    reference.index = reference.index.as_unit("ns")
    pd.testing.assert_frame_equal(
        actual_indicators[fields], reference, check_dtype=False, check_exact=True
    )
    trades = sorted(stats["trades"], key=lambda t: t["open_timestamp"])
    opens = {pd.Timestamp(t["open_date"]): t for t in trades}
    require(len(opens) == len(trades), "Duplicate open clock")
    selected = expected.loc[
        expected.date.ge(pd.Timestamp(plan["start"]))
        & expected.date.lt(pd.Timestamp(plan["end"]) - pd.Timedelta(minutes=5))
        & expected.session_boundary
        & expected.session_known
        & expected.session_target.ne(0)
    ].copy()
    if candidate["strategy"] == "PassiveLong":
        require(
            len(trades) == 1 and not trades[0]["is_short"],
            "Passive policy did not hold one actual long",
        )
        require(
            pd.Timestamp(trades[0]["open_date"])
            == pd.Timestamp(plan["start"]) + pd.Timedelta(minutes=5),
            "Passive initial clock",
        )
        return {
            "passed": True,
            "actual_indicator_rows": len(actual_indicators),
            "accepted_intents": 1,
            "unfunded_intents": 0,
            "scope": "One continuous native long; no fabricated per-session passive rebalances",
        }
    selected["execution_time"] = selected.date + pd.Timedelta(minutes=5)
    limits = json.loads(Path(plan["market_snapshot"]["path"]).read_text())["markets"][pair]
    step = Decimal(str(limits["precision"]["amount"]))
    funded = 0
    rejected = []
    for row in selected.itertuples():
        clock = row.execution_time
        # The source cohort must already be closed five minutes before entry.
        old = [t for t in trades if pd.Timestamp(t["open_date"]) < clock]
        require(
            all(pd.Timestamp(t["close_date"]) < clock for t in old),
            "Unclosed earlier funded session",
        )
        cash = 500.0 + sum(t["profit_abs"] for t in old)
        available = cash * 0.99
        price = float(original.loc[clock, "open"])
        minimum = max(
            limits["limits"]["cost"]["min"] * 1.05 / 0.95,
            limits["limits"]["amount"]["min"] * price * 1.05,
        )
        quantity = (Decimal(str(available)) / Decimal(str(price)) / step).to_integral_value(
            rounding=ROUND_DOWN
        ) * step
        if clock not in opens:
            require(available < minimum or quantity == 0, "Unexplained unfilled native intent")
            rejected.append(
                {
                    "time": str(clock),
                    "actual_prior_closed_cash": cash,
                    "investable": available,
                    "native_minimum_stake": minimum,
                    "reason": "Frozen native minimum stake/quantity; no upsizing",
                }
            )
            continue
        trade = opens[clock]
        require(available >= minimum, "Native fill below admitted minimum")
        require(
            abs(trade["amount"] - float(quantity)) < 1e-9,
            "Native quantity differs from prior actual cash/lot floor",
        )
        require(trade["is_short"] == (row.session_target == -1), "Actual source direction mismatch")
        require(abs(trade["open_rate"] - price) < 1e-8, "Actual native open price mismatch")
        require(len(trade["orders"]) == 2, "Unexpected adjustment/order policy")
        if trade["exit_reason"] != "force_exit":
            require(trade["exit_reason"] == "session_end", "Unexpected economic exit")
            require(
                pd.Timestamp(trade["close_date"]) - clock == pd.Timedelta(hours=11, minutes=55),
                "Session holding clock mismatch",
            )
        funded += 1
    require(funded == len(trades), "Actual trade absent from frozen intent grid")
    return {
        "passed": True,
        "actual_indicator_rows": len(actual_indicators),
        "accepted_intents": funded,
        "scheduled_nonzero_intents": len(selected),
        "unfunded_intents": len(rejected),
        "unfunded": rejected,
        "actual_cash_and_quantity_reconciled": True,
        "not_an_independent_account_return_engine": True,
    }


def main():
    plan = json.loads((HERE / "plan.json").read_text())
    out = Path(plan["output_root"])
    complete = json.loads((HERE / "execution_complete.json").read_text())
    require(complete["accounts"] == 12, "Matrix incomplete")
    grid = pd.date_range(plan["start"], plan["comparison_end"], freq="h").as_unit("ns")
    navs, rows, evidence = {}, [], []
    for cost in plan["total_cost_bps_per_side"]:
        for candidate in plan["candidates"]:
            verify(plan, candidate)
            name = f"{candidate['name']}_{cost}bps"
            path = out / name
            receipt = json.loads((path / "process_receipt.json").read_text())
            require(
                receipt["returncode"] == 0
                and receipt["result_sha256"] == sha(path / "result.json"),
                "Failed or altered worker",
            )
            native, archive, config = read_native(path, candidate["strategy"])
            require(
                native["dry_run_wallet"] == 500 and config["fee"] == cost / 10000,
                "Wrong archived cash/fee",
            )
            require(
                native["max_open_trades"] == 1
                and native["timeframe"] == "5m"
                and native["trading_mode"] == "futures",
                "Wrong native execution contract",
            )
            require(all(t["leverage"] == 1 for t in native["trades"]), "Unexpected leverage")
            cash_audit = audit_trades(native)
            save(path / "independent_archive_cash_recheck.json", cash_audit)
            intent_audit = funded_intents(candidate, native, plan, path)
            save(path / "intent_reconciliation.json", intent_audit)
            frame = pd.read_parquet(path / "hourly_equity.parquet")
            nav = frame.set_index("date").hourly_mark_equity.loc[
                plan["start"] : plan["comparison_end"]
            ]
            require(nav.index.as_unit("ns").equals(grid), "Incomplete/duplicate hourly account")
            require(nav.iloc[0] == 500, "Wrong start equity")
            full = metrics(nav)
            result = json.loads((path / "result.json").read_text())
            require(
                abs(full["net_return"] - result["periods"][0]["net_return"]) < 1e-12,
                "Native return mismatch",
            )
            require(
                abs(
                    full["maximum_hourly_mark_drawdown"]
                    - result["periods"][0]["hourly_mark_drawdown"]
                )
                < 1e-12,
                "Native drawdown mismatch",
            )
            navs[name] = nav
            rows.append(
                {
                    "account": candidate["name"],
                    "cost_bps_per_side": cost,
                    **full,
                    "native_trades": len(native["trades"]),
                    "natural_closes": sum(
                        t["exit_reason"] != "force_exit" for t in native["trades"]
                    ),
                    "forced_closes": sum(
                        t["exit_reason"] == "force_exit" for t in native["trades"]
                    ),
                    "unfunded_intents": intent_audit["unfunded_intents"],
                    "native_terminal_return_separate": native["profit_total"],
                    "native_funding_usdt": sum(t["funding_fees"] for t in native["trades"]),
                }
            )
            evidence.append(
                {
                    "account": name,
                    "archive": str(archive),
                    "sha256": sha(archive),
                    "actual_native_indicator_rows_checked": intent_audit["actual_indicator_rows"],
                    "hourly_points": len(nav),
                    "cash_audit_passed": cash_audit["passed"],
                }
            )
    combinations = []
    gates = []
    for cost in plan["total_cost_bps_per_side"]:
        values = {}
        for policy in ["SourceSessions", "TimedLongSessions", "PassiveLong"]:
            left = navs[f"{policy}_BTC_{cost}bps"]
            right = navs[f"{policy}_ETH_{cost}bps"]
            nav = combine(left, right)
            nav.to_frame("native_sleeve_sum_usdt").to_parquet(
                HERE / f"{policy}_{cost}bps_combined.parquet"
            )
            periods = [
                {"name": p["name"], **metrics(nav.loc[p["start"] : p["end"]])}
                for p in plan["periods"]
            ]
            correlation = (
                left.resample("1D")
                .last()
                .pct_change()
                .corr(right.resample("1D").last().pct_change())
            )
            record = {
                "policy": policy,
                "cost_bps_per_side": cost,
                "periods": periods,
                "daily_leg_correlation": float(correlation),
                "actual_independent_wallets": [500, 500],
                "cash_transfers": False,
            }
            combinations.append(record)
            values[policy] = periods
        source = values["SourceSessions"]
        gate = all(p["net_return"] > 0 and p["maximum_hourly_mark_drawdown"] <= 0.5 for p in source)
        gate &= (
            source[0]["net_return"] > values["TimedLongSessions"][0]["net_return"]
            and source[0]["net_return"] > values["PassiveLong"][0]["net_return"]
        )
        gates.append({"cost_bps_per_side": cost, "passed": bool(gate)})
    save(
        HERE / "comparison.json",
        {
            "accounts": rows,
            "combinations": combinations,
            "gates": gates,
            "valuation": (
                "Native pre-bar hourly mark NAV, open PNL and reserved close fee; "
                "terminal forced liquidation separate"
            ),
        },
    )
    pd.DataFrame(rows).to_csv(HERE / "comparison.csv", index=False)
    passed = all(g["passed"] for g in gates)
    save(
        HERE / "verification.json",
        {
            "passed_within_scope": True,
            "actual_successful_native_accounts": len(rows),
            "actual_hourly_points": len(grid) * len(rows),
            "evidence": evidence,
            "native_matrix_seals_rechecked": True,
            "source_boundary_and_prefix_checks": json.loads((HERE / "preflight.json").read_text())[
                "passed"
            ],
            "funding_archive_check": json.loads((HERE / "funding_audit.json").read_text())[
                "passed"
            ],
            "numeric_gates": gates,
            "source_selection_bias_or_independent_oos_resolved": False,
            "actual_exchange_fills_certified": False,
        },
    )
    save(
        HERE / "status.json",
        {
            "status": "COMPLETE_NATIVE_SESSION_MATRIX_NUMERIC_PASS"
            if passed
            else "COMPLETE_NATIVE_SESSION_MATRIX_REJECTED",
            "native_accounts": len(rows),
            "numeric_pass": passed,
            "new_independently_qualified_alpha": False,
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
