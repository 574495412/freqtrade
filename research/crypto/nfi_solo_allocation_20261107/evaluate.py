"""Observe five-minute risk and apply the registered gate to native A08 accounts."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi_solo_allocation_20261107.run import (
    A04,
    LONG_PLAN,
    OUT,
    REGISTRATION,
    RESULT,
    SHORT_PLAN,
    account,
    read,
)
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation, temporal
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked,
    load_module,
    save,
    sha,
)


def revalue():
    native = read(SHORT_PLAN)
    receipt = read(OUT / "temporal_audit.json")
    books = {f"temporal_{cost}bps": row for cost, row in receipt.items()}
    oracle = load_module("a08_account_oracle", mark_valuation.ORACLE)
    temporal.OUT = OUT
    mark_valuation.OUT = OUT / "5m"
    hour = pd.date_range(native["start"], native["comparison_end"], freq="h").as_unit("us")
    grid = pd.date_range(native["start"], native["comparison_end"], freq="5min").as_unit("us")
    holdings, sources, checks = mark_valuation.trades_and_checks(
        {"accounts": books}, native, oracle, hour
    )
    save(OUT / "hourly_parity.json", checks)
    if max(checks.values()) > 1e-5:
        raise ValueError("Native hourly NAV did not independently reproduce")
    with ThreadPoolExecutor(max_workers=8) as pool:
        prices = list(pool.map(lambda s: mark_valuation.month_mark(s, grid), sources))
    marks = {s: p for s, p, _ in prices}
    for symbol, prices5 in marks.items():
        archived = sources[symbol][1].set_index("date").open.reindex(hour).to_numpy()
        if not np.allclose(prices5.reindex(hour), archived, rtol=1e-10, atol=1e-12):
            raise ValueError(f"Mark price clocks disagree: {symbol}")
    mark = {}
    for name, trades in holdings.items():
        net = np.full(len(grid), native["initial_equity_usdt"], dtype=float)
        clocks = grid.as_unit("ms").asi8
        for t in trades:
            symbol = t["pair"]
            funding, prior_mark = sources[symbol]
            oracle.contribute(t, clocks, marks[symbol].to_numpy(), funding, prior_mark, net)
        dd = 1 - net / np.maximum.accumulate(net)
        path = OUT / "5m" / f"{name}.parquet"
        pd.DataFrame({"date": grid, "equity": net, "drawdown": dd}).to_parquet(path, index=False)
        if not np.isfinite(net).all():
            raise ValueError("Nonfinite 5m account")
        mark[name] = {
            "drawdown": float(dd.max()),
            "points": len(net),
            "curve_sha256": sha(path),
            "hourly_error_usdt": checks[name],
        }
    return receipt, mark, {symbol: row for symbol, _, row in prices}


def temporal_gates(receipt, mark):
    older = read(A04 / "temporal_result.json")["accounts"]
    risk = read(A04 / "mark_result.json")["accounts"]
    failures, matched = {}, {}
    for cost in ("15", "8"):
        original_key, flat_key = f"nfi-x8-1x_{cost}bps", f"FlatTopBoost_{cost}bps"
        candidate = receipt[cost]["periods"]["continuous"]
        original = older[original_key]["periods"]["continuous"]["net_return"]
        fixed = older[flat_key]["periods"]["continuous"]["net_return"]
        d = mark[f"temporal_{cost}bps"]["drawdown"]
        d0, d1 = (risk[k]["drawdown_5m_mark"] for k in (original_key, flat_key))
        checks = {
            "net_return": candidate["net_return"] > max(0, original),
            "risk": d < d1 and d - d0 <= 0.01,
            "fixed_return_drawdown": candidate["net_return"] * d1 > fixed * d,
            "sample": candidate["completed_campaigns"] >= 20
            and receipt[cost]["matched_filled_top_coin_campaigns"] >= 10,
        }
        failures[cost] = [key for key, passed in checks.items() if not passed]
        matched[cost] = {
            "candidate_return": candidate["net_return"],
            "original_return": original,
            "fixed_return": fixed,
            "candidate_5m_drawdown": d,
            "original_5m_drawdown": d0,
            "fixed_5m_drawdown": d1,
        }
    return failures, matched


def long_gates(accounts):
    controls = read(A04 / "result.json")["accounts"]
    result = {}
    for cost, row in accounts.items():
        original = controls[f"FlatTopBoost_{cost}bps"]["periods"]
        candidate = row["periods"]
        full = candidate["continuous"]
        fixed = original["continuous"]
        checks = {
            "net_returns": all(
                v["net_return"] > original[k]["baseline_return"] for k, v in candidate.items()
            ),
            "risk": full["hourly_mark_drawdown"] - fixed["baseline_drawdown"] <= 0.01,
            "fixed_return_drawdown": full["net_return"] * fixed["max_drawdown"]
            > fixed["net_return"] * full["hourly_mark_drawdown"],
        }
        result[cost] = [key for key, passed in checks.items() if not passed]
    return result


def main():
    for source in read(REGISTRATION)["sources"]:
        checked(source)
    receipt, mark, sources = revalue()
    failures, matched = temporal_gates(receipt, mark)
    report = {
        "registration_sha256": sha(REGISTRATION),
        "audit_source_sha256": sha(Path(__file__)),
        "temporal_accounts": receipt,
        "mark_audits": mark,
        "mark_sources": sources,
        "temporal_matched": matched,
        "temporal_failed_gates": failures,
        "long_stage_admitted": not any(failures.values()),
        "paper_eligible": False,
    }
    save(RESULT, report)
    print(failures, matched, flush=True)
    if report["long_stage_admitted"]:
        accounts = {str(cost): account(LONG_PLAN, "long", cost) for cost in (15, 8)}
        report.update(long_accounts=accounts, long_failed_gates=long_gates(accounts))
        save(RESULT, report)


if __name__ == "__main__":
    main()
