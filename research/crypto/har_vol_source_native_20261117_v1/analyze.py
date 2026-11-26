"""Independent native-fill reconstruction and fixed actual500wallet sums."""

import hashlib
import importlib.util
import json
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



def main():
    plan = read(HERE / "plan.json")
    assert read(HERE / "execution_complete.json")["accounts"] == 8
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
    for candidate in plan["candidates"]:
        nav, row = audit.audit(candidate, plan, grid, oracle)
        navs[candidate["name"]] = nav
        accounts.append(row)
        print("AUDITED", candidate["name"], row["periods"][0]["net_return"], flush=True)
    # Frozen parents are unchanged evidence; no new alpha or wallet is attributed.
    for path, digest in read(HERE / "protocol.json")["inputs"].items():
        assert sha(path) == digest, path
        entries = read(path)["files"]
        if isinstance(entries, dict):
            entries = [dict(path=str(Path(path).parent / name), sha256=digest) for name, digest in entries.items()]
        for r in entries:
            assert sha(r["path"]) == r["sha256"], r["path"]
    trend = HERE.with_name("trendcore_source_native_20261117_v1")
    up = HERE.with_name("universal_allocation_native_20261116_v1")
    nfi = HERE.with_name("nfi_carry_matched_native_20261115_v1")
    for parent, roles in [(trend, ["trendcore"]), (up, ["crp"]), (nfi, ["nfi_spot", "benchmark"])]:
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
        if row["role"] not in ["har","naive"]:
            continue
        p = row["periods"]
        controls = [
            c
            for c in accounts
            if c["capital"] == row["capital"]
            and c["scenario"] == row["scenario"]
            and c["role"] in ["trendcore", "nfi_spot", "crp", "benchmark"]
        ]
        assert len(controls) == 4
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
    # Add model-matched naive control after all native accounts are audited.
    for gate in gates:
        if gate['account'].startswith('har_'):
            candidate=next(r for r in accounts if r['account']==gate['account'])
            naive=next(r for r in accounts if r['account']==gate['account'].replace('har_','naive_',1))
            better=candidate['periods'][0]['net_return']>naive['periods'][0]['net_return']
            gate['beats_native_naive']=better
            gate['frozen_financial_gate']=gate['frozen_financial_gate']and better
    for scenario in ["conservative", "lower"]:
        controls = [
            r
            for r in accounts
            if r["capital"] == 1000
            and r["scenario"] == scenario
            and r["role"] in ["har", "naive", "trendcore", "nfi_spot", "crp", "benchmark"]
        ]
        best = max(controls, key=lambda r: r["periods"][0]["net_return"])
        for other in ["har", "naive"]:
            names = [other + "_500_" + scenario, "trendcore_500_" + scenario]
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
            name = other + "_trendcore_1000_" + scenario
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
    accepted = all(g["frozen_financial_gate"] for g in gates if g["account"].startswith("har_"))
    result = dict(
        new_native_wallets=8,
        new_native_trades=sum(r.get("actual_native_trades", 0) for r in accounts),
        new_simulated_fills=sum(r.get("filled_orders", 0) for r in accounts),
        new_hour_points=8 * len(grid),
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
        source_predictor_parity_verified=True,source_account_simulator_executed=False,
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
