"""Independent native-fill reconstruction and fixed actual500wallet sums."""

import gzip
import hashlib
import importlib.util
import json
import math
from decimal import Decimal as D, ROUND_DOWN
from pathlib import Path

import pandas as pd

HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]


def read(p):
    return json.loads(Path(p).read_text())


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(p, x):
    Path(p).write_text(json.dumps(x, indent=2, allow_nan=False) + "\n")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def budget_audit(candidate, plan, nav, audit):
    directory = Path(plan["output_root"]) / (candidate["name"] + f"_{candidate['cost_bps']}bps")
    with gzip.open(directory / "strategy_trace.json.gz", "rt") as f:
        trace = json.load(f)
    weights = pd.DataFrame(trace["weights"]).set_index("day")
    weights.index = pd.to_datetime(weights.index, utc=True)
    ref = pd.read_parquet(
        Path(plan["output_root"])
        / "preflight_trendcore_1000_conservative_admission1/native_weights.parquet"
    )
    pd.testing.assert_frame_equal(weights, ref, check_freq=False, check_names=False)
    stats, _ = audit.native_stats(directory, candidate["strategy"])
    orders = {p: [] for p in candidate["pairs"]}
    for trade in stats["trades"]:
        for o in trade["orders"]:
            orders[trade["pair"]].append(o)
            if trade["exit_reason"] == "force_exit" and o is trade["orders"][-1]:
                continue
            stamp = pd.to_datetime(o["order_filled_timestamp"], unit="ms", utc=True)
            assert stamp.hour % 6 == 0 and stamp.minute == stamp.second == 0
    market = read(candidate["market_snapshot"]["path"])["markets"]
    maximum = 0.0
    checked = 0
    config = read(directory / "config.json")
    reserve = 1 + config.get("amount_reserve_percent", 0.05)
    for row in trace["budgets"]:
        stamp = pd.Timestamp(row["clock"])
        assert stamp.hour % 6 == 0 and stamp.minute == 0
        maximum = max(maximum, abs(row["native_marked_equity"] - nav.loc[stamp]))
        assert maximum < 1e-6
        for pair, target in row["target_quantities"].items():
            price = row["prices"][pair]
            equity = row["native_marked_equity"]
            weight = float(weights.at[stamp, pair])
            held = math.fsum(
                o["amount"] * (1 if o["ft_is_entry"] else -1)
                for o in orders[pair]
                if o["order_filled_timestamp"] < stamp.timestamp() * 1000
            )
            if weight != 0 and abs(0.99 * weight - held * price / equity) <= 0.05:
                expected = held
            else:
                step = D(str(market[pair]["precision"]["amount"]))
                expected = float(
                    (
                        D(".99") * D(str(equity)) * D(str(weight)) / D(str(price)) / step
                    ).to_integral_value(rounding=ROUND_DOWN)
                    * step
                )
            limits = market[pair]["limits"]
            minimum = max(
                (limits["cost"]["min"] or 0) * 1.5, (limits["amount"]["min"] or 0) * price * reserve
            )
            if expected * price < minimum:
                expected = 0.0
            assert abs(target - expected) < 1e-10, (
                candidate["name"],
                stamp,
                pair,
                target,
                expected,
            )
            checked += 1
    expected_grid = pd.date_range("2025-01-01T06:00Z", "2026-08-31T18:00Z", freq="6h")
    actual = pd.DatetimeIndex(pd.to_datetime([r["clock"] for r in trace["budgets"]], utc=True))
    assert actual.as_unit("ns").equals(expected_grid.as_unit("ns"))
    return dict(
        account=candidate["name"],
        passed=True,
        budgets=len(actual),
        independently_checked_quantities=checked,
        maximum_budget_nav_error_usdt=maximum,
        nonterminal_fills_only_after_complete_sixhour_signal=True,
        source_weights_identical_across_four_wallets=True,
    )


def main():
    plan = read(HERE / "plan.json")
    assert read(HERE / "execution_complete.json")["accounts"] == 4
    for path, digest in read(HERE / "implementation_freeze.json").items():
        assert sha(path) == digest, path
    for path, digest in read(HERE / "analysis_freeze.json").items():
        assert sha(path) == digest, path
    audit = load("independent_fill_audit", HERE / "audit_accounts.py")
    oracle_path = ROOT.parent / "TradingAgents/research_tools/nfi_reproduction/audit_original.py"
    oracle = load("independent_cash_oracle", oracle_path)
    grid = pd.date_range(plan["start"], plan["comparison_end"], freq="h").as_unit("ns")
    accounts = []
    navs = {}
    budgets = []
    for candidate in plan["candidates"]:
        nav, row = audit.audit(candidate, plan, grid, oracle)
        navs[candidate["name"]] = nav
        accounts.append(row)
        budgets.append(budget_audit(candidate, plan, nav, audit))
        print("AUDITED", candidate["name"], row["periods"][0]["net_return"], flush=True)
    # Frozen parents are unchanged evidence; no new alpha or wallet is attributed.
    for path, digest in read(HERE / "protocol.json")["inputs"].items():
        assert sha(path) == digest, path
        entries = read(path)["files"]
        if isinstance(entries, dict):
            entries = [dict(path=str(Path(path).parent / name), sha256=digest) for name, digest in entries.items()]
        for r in entries:
            assert sha(r["path"]) == r["sha256"], r["path"]
    up = HERE.with_name("universal_allocation_native_20261116_v1")
    nfi = HERE.with_name("nfi_carry_matched_native_20261115_v1")
    for parent, roles in [(up, ["crp"]), (nfi, ["nfi_spot", "benchmark"])]:
        pp = read(parent / "plan.json")
        for row in read(parent / "results.json")["accounts"]:
            if row["role"] not in roles:
                continue
            name = row["account"]
            if row["role"] == "benchmark":
                path = HERE.with_name("paired_carry_reservation_native_20261115_v2") / (
                    name + ".parquet"
                )
            else:
                c = next(c for c in pp["candidates"] if c["name"] == name)
                directory = Path(pp["output_root"]) / (name + f"_{c['cost_bps']}bps")
                _, archive = audit.native_stats(directory, c["strategy"])
                assert sha(archive) == row["archive_sha256"]
                path = directory / "hourly_equity.parquet"
            nav = pd.read_parquet(path).set_index("date").hourly_mark_equity
            assert nav.index.as_unit("ns").equals(grid)
            periods = audit.periods(nav, plan)
            for current, old in zip(periods, row["periods"], strict=True):
                assert abs(current["net_return"] - old["net_return"]) < 1e-12
            navs[name] = nav
            accounts.append(
                dict(
                    account=name,
                    role=row["role"],
                    capital=row["capital"],
                    scenario=row["scenario"],
                    periods=periods,
                    reused=True,
                    source_curve=dict(path=str(path), sha256=sha(path)),
                )
            )
    gates = []
    portfolios = []
    for row in accounts:
        if not row["account"].startswith("trendcore_"):
            continue
        p = row["periods"]
        controls = [
            c
            for c in accounts
            if c["capital"] == row["capital"]
            and c["scenario"] == row["scenario"]
            and c["role"] in ["nfi_spot", "crp", "benchmark"]
        ]
        assert len(controls) == 3
        numeric = (
            all(v["net_return"] > 0 for v in p)
            and p[0]["hourly_mark_drawdown"] <= 0.3
            and p[0]["daily_nav_sharpe"] is not None
            and p[0]["daily_nav_sharpe"] > 0.5
        )
        beats = all(p[0]["net_return"] > c["periods"][0]["net_return"] for c in controls)
        gates.append(
            dict(
                account=row["account"],
                positive_all_periods=all(v["net_return"] > 0 for v in p),
                numeric_gate=numeric,
                beats_all_matched_spot_controls=beats,
                frozen_financial_gate=numeric and beats,
            )
        )
    for scenario in ["conservative", "lower"]:
        controls = [
            r
            for r in accounts
            if r["capital"] == 1000
            and r["scenario"] == scenario
            and r["role"] in ["nfi_spot", "crp", "benchmark"]
        ]
        best = max(controls, key=lambda r: r["periods"][0]["net_return"])
        for other in ["nfi_spot", "crp"]:
            names = ["trendcore_500_" + scenario, other + "_500_" + scenario]
            nav = sum(navs[n] for n in names)
            assert nav.iloc[0] == 1000
            p = audit.periods(nav, plan)
            beats = p[0]["net_return"] > max(0, best["periods"][0]["net_return"])
            no_worse_dd = p[0]["hourly_mark_drawdown"] <= best["periods"][0]["hourly_mark_drawdown"]
            numeric = (
                all(v["net_return"] > 0 for v in p)
                and p[0]["hourly_mark_drawdown"] <= 0.3
                and p[0]["daily_nav_sharpe"] is not None
                and p[0]["daily_nav_sharpe"] > 0.5
            )
            name = "trendcore_" + other + "_1000_" + scenario
            pd.DataFrame(dict(date=nav.index, hourly_mark_equity=nav.to_numpy())).to_parquet(
                HERE / (name + ".parquet"), index=False
            )
            portfolios.append(
                dict(
                    name=name,
                    sleeves=names,
                    actual500plus500=True,
                    transfers=False,
                    scaled_curves=False,
                    periods=p,
                    best_matched_spot_control=best["account"],
                    beats_best_control=beats,
                    no_dd_worsening=no_worse_dd,
                    numeric_gate=numeric,
                    frozen_financial_gate=numeric and beats and no_worse_dd,
                )
            )
    save(HERE / "budget_audit.json", dict(passed=True, accounts=budgets))
    accepted = all(g["frozen_financial_gate"] for g in gates)
    result = dict(
        new_native_wallets=4,
        new_native_trades=sum(r.get("actual_native_trades", 0) for r in accounts),
        new_simulated_fills=sum(r.get("filled_orders", 0) for r in accounts),
        new_hour_points=4 * len(grid),
        maximum_independent_nav_error_usdt=max(
            r.get("maximum_nav_error_usdt", 0) for r in accounts
        ),
        accounts=accounts,
        standalone_gates=gates,
        portfolios=portfolios,
        independent_cash_oracle=dict(path=str(oracle_path), sha256=sha(oracle_path)),
        financial_gate_passed=accepted,
        new_independent_alpha_proven=False,
        independent_oos=False,
        source_default_oi_reproduced=False,
        historical_venue_rules_certified=False,
        orders_enabled=False,
        production_eligible=False,
    )
    save(HERE / "results.json", result)
    print(
        "COMPLETE",
        result["new_native_trades"],
        "trades",
        result["new_simulated_fills"],
        "fills;",
        [(p["name"], p["periods"][0]["net_return"]) for p in portfolios],
        flush=True,
    )


if __name__ == "__main__":
    main()
