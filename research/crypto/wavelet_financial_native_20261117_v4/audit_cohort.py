"""Verify actual reference contracts before ranking the new native accounts."""

# ruff: noqa: S101
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
FIELDS = ["open", "high", "low", "close", "volume"]


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def common_bars(root, pair, timeframe, plan):
    path = Path(root) / (pair.replace("/", "_") + "-" + timeframe + ".feather")
    frame = pd.read_feather(path)
    frame = frame.loc[
        frame.date.ge(pd.Timestamp(plan["start"])) & frame.date.lt(pd.Timestamp(plan["end"]))
    ].set_index("date")[FIELDS]
    frame.index = frame.index.as_unit("ns")
    return frame


def compare_bars(parent, active):
    same_calendar = parent.index.equals(active.index)
    if not same_calendar:
        return {"same_calendar": False, "all_fields_exact": False, "max_abs_difference": None}
    diff = {
        name: float(np.max(np.abs(parent[name].to_numpy() - active[name].to_numpy())))
        for name in FIELDS
    }
    return {
        "same_calendar": True,
        "all_fields_exact": all(v == 0 for v in diff.values()),
        "max_abs_difference": diff,
    }


def main():
    output = HERE / "cohort_contract_audit.json"
    assert not output.exists(), "Preserve previous contract audit"
    plan = read(HERE / "plan_attempt2.json")
    contract = read(HERE / "comparison_contract.json")
    grid = pd.date_range(plan["start"], plan["comparison_end"], freq="h").as_unit("ns")
    active = {
        (p, t): common_bars(plan["datadir"], p, t, plan)
        for p in plan["pairs"]
        for t in ["5m", "1h"]
    }
    data_cache = {}
    rows = []
    for control in contract["controls"]:
        curve = control["hourly_equity"]
        assert sha(curve["path"]) == curve["sha256"]
        nav = pd.read_parquet(curve["path"]).set_index("date").hourly_mark_equity
        assert nav.index.as_unit("ns").equals(grid)
        assert nav.iloc[0] == control["capital"] and nav.notna().all()
        row = {
            "account": control["name"],
            "capital": control["capital"],
            "cost_bps": control["cost_bps"],
            "calendar_hours": len(nav),
            "native_account": "source_plan" in control,
        }
        if "source_plan" in control:
            assert sha(control["source_plan"]["path"]) == control["source_plan"]["sha256"]
            parent = read(control["source_plan"]["path"])
            for key in ["start", "end", "comparison_end", "engine_commit", "allowed_leverage"]:
                assert parent[key] == plan[key], (control["name"], key)
            candidate = next(c for c in parent["candidates"] if c["name"] == control["name"])
            cfg = read(candidate["config"]["path"])
            assert sha(candidate["config"]["path"]) == candidate["config"]["sha256"]
            assert cfg["dry_run"] and cfg["dry_run_wallet"] == control["capital"]
            assert cfg["trading_mode"] == "spot" and cfg["margin_mode"] == ""
            assert cfg["stake_currency"] == "USDT" and cfg["tradable_balance_ratio"] == 0.99
            assert (
                cfg["max_open_trades"] == 2 and cfg["exchange"]["pair_whitelist"] == plan["pairs"]
            )
            assert cfg["fee"] == control["cost_bps"] / 10000
            assert candidate["market_snapshot"] == plan["market_snapshot"]
            result = read(control["result"]["path"])
            assert sha(control["result"]["path"]) == control["result"]["sha256"]
            assert result["engine_source"] == str(
                Path(plan["engine_source"]) / "freqtrade/__init__.py"
            )
            assert result["native_terminal_not_used_for_ranking"] and not result["orders_enabled"]
            directory = parent["datadir"]
            if directory not in data_cache:
                data_cache[directory] = {
                    pair + "|" + tf: compare_bars(common_bars(directory, pair, tf, plan), frame)
                    for (pair, tf), frame in active.items()
                }
            row.update(
                source_plan=control["source_plan"],
                cash_usdt_spot1x_contract_equal=True,
                execution_timeframe=cfg["timeframe"],
                detail_timeframe=cfg.get("timeframe_detail"),
                order_types=cfg["order_types"],
                data_comparison=data_cache[directory],
            )
        else:
            row.update(
                evidence_type="ANALYTICAL_BENCHMARK_REFERENCE_NOT_NATIVE_WALLET",
                native_execution_not_established=True,
            )
        rows.append(row)
    aligned = all(
        v["all_fields_exact"]
        for r in rows
        if r["native_account"]
        for v in r["data_comparison"].values()
    )
    report = {
        "passed_registered_contract_checks": True,
        "all_native_reference_common_OHLCV_exact": aligned,
        "native_reference_accounts": sum(r["native_account"] for r in rows),
        "analytical_references": sum(not r["native_account"] for r in rows),
        "references": rows,
        "execution_policy_difference": (
            "NFI uses source limit orders on5m;wavelet market orders on15m with5m detail. "
            "Equal flat fee and native OHLCV matching are research assumptions,not equal "
            "authenticated venue fills or queue proof."
        ),
        "global_40perpetual_ranking_allowed": False,
        "new_account_performance_proven": False,
    }
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "registered_contracts_passed": True,
                "native_reference_accounts": 12,
                "analytical_references": 2,
                "common_OHLCV_exact": aligned,
                "new_account_performance_proven": False,
            }
        )
    )


if __name__ == "__main__":
    main()
