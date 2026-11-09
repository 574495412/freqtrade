"""Audit actual native archives and observed equity; never simulate substitute fills."""

import json
import math
from zipfile import ZipFile

import numpy as np
import pandas as pd

from research.crypto.nfi80_capacity_20261109.study import (
    CASES,
    HERE,
    OUT,
    PROTOCOL,
    read,
    ref,
    save,
)


def account(name, cost):
    folder = OUT / f"{name}_{cost}bps"
    result = read(folder / "result.json")
    completion = read(folder / "completion.json")
    config = read(folder / "effective_config.json")
    policy = read(PROTOCOL)
    leverage, slots = CASES[name]
    if completion["returncode"] != 0 or not read(folder / "fill_cash_audit.json")["passed"]:
        raise ValueError("Native account is not completed and cash-audited")
    if (
        config["max_open_trades"] != slots
        or config["exchange"]["pair_whitelist"] != policy["pairs"]
    ):
        raise ValueError("Effective account contract differs")
    if not config["dry_run"] or any(
        config["exchange"].get(k) for k in ("key", "secret", "password")
    ):
        raise ValueError("Only anonymous research accounts are admissible")
    archives = list((folder / "native_results").glob("*.zip"))
    if len(archives) != 1:
        raise ValueError("Expected exactly one native archive")
    with ZipFile(archives[0]) as z:
        files = [p for p in z.namelist() if p.endswith(".json") and not p.endswith("_config.json")]
        if len(files) != 1:
            raise ValueError("Ambiguous native account result")
        stats = next(iter(json.loads(z.read(files[0]))["strategy"].values()))
    trades = stats["trades"]
    if any(t["leverage"] != leverage for t in trades):
        raise ValueError("Unregistered filled-trade leverage")
    frame = pd.read_parquet(folder / "five_minute_equity.parquet")
    frame = frame.loc[
        frame.date.between(pd.Timestamp(policy["start"]), pd.Timestamp(policy["comparison_end"]))
    ]
    expected = pd.date_range(policy["start"], policy["comparison_end"], freq="5min").as_unit("ns")
    if not pd.DatetimeIndex(frame.date).as_unit("ns").equals(expected):
        raise ValueError("Five-minute account observation calendar incomplete")
    equity = frame.marked_equity.to_numpy(dtype=float)
    if not np.isfinite(equity).all():
        raise ValueError("Nonfinite observed equity")
    ret = float(equity[-1] / equity[0] - 1)
    drawdown = float(np.max(1 - equity / np.maximum.accumulate(equity)))
    if (
        abs(ret - result["periods"][0]["net_return"]) > 1e-10
        or abs(drawdown - result["periods"][0]["five_minute_mark_drawdown"]) > 1e-10
    ):
        raise ValueError("Independent curve metrics differ from native-run report")
    end_ms = int(pd.Timestamp(policy["comparison_end"]).timestamp() * 1000)
    natural = [
        t for t in trades if t["exit_reason"] != "force_exit" and t["close_timestamp"] < end_ms
    ]
    entries = [[o for o in t["orders"] if o["ft_is_entry"] and o["amount"] > 0] for t in trades]
    liquidations = sum(t["exit_reason"] == "liquidation" for t in trades)
    margin = frame.open_margin_usdt.fillna(0).to_numpy() / equity
    return {
        "case": name,
        "leverage": leverage,
        "max_open_trades": slots,
        "cost_bps_per_side": cost,
        "net_return_pct": ret * 100,
        "net_profit_usdt": float(equity[-1] - equity[0]),
        "ending_marked_equity_usdt": float(equity[-1]),
        "maximum_5m_mark_drawdown_pct": drawdown * 100,
        "natural_completed_trades": len(natural),
        "natural_campaign_win_rate_pct": 100
        * sum(t["profit_abs"] > 0 for t in natural)
        / len(natural)
        if natural
        else None,
        "native_exported_trades_including_forced_terminal": len(trades),
        "actual_entry_fills": sum(map(len, entries)),
        "actual_addition_fills": sum(max(0, len(e) - 1) for e in entries),
        "max_entries_per_trade": max(map(len, entries), default=0),
        "observed_peak_open_positions": int(frame.open_trades.max()),
        "observed_peak_margin_to_marked_equity_pct": float(np.max(margin) * 100),
        "native_modeled_liquidations": liquidations,
        "native_terminal_return_pct_separate": result["native_terminal_return"] * 100,
        "native_model_funding_usdt_including_terminal": math.fsum(
            t["funding_fees"] for t in trades
        ),
        "research_risk_gate_passed": drawdown <= policy["research_drawdown_limit"]
        and liquidations == 0,
        "valuation_points": len(frame),
        "periods": result["periods"],
        "native_archive": ref(archives[0]),
        "native_result": ref(folder / "result.json"),
        "observed_equity": ref(folder / "five_minute_equity.parquet"),
        "cash_audit": ref(folder / "fill_cash_audit.json"),
        "completion": ref(folder / "completion.json"),
    }


def main():
    rows = [account(name, cost) for cost in (15, 8) for name in CASES]
    rankings = {}
    for cost in (15, 8):
        ranked = sorted(
            [r for r in rows if r["cost_bps_per_side"] == cost],
            key=lambda r: r["net_return_pct"],
            reverse=True,
        )
        rankings[str(cost)] = {
            "ordered_by_net_return": [r["case"] for r in ranked],
            "highest_net_return": ranked[0]["case"],
            "highest_return_passing_frozen_research_risk_gate": next(
                (r["case"] for r in ranked if r["research_risk_gate_passed"]), None
            ),
        }
    result = {
        "protocol": ref(PROTOCOL),
        "accounts": rows,
        "rankings": rankings,
        "basis": (
            "Same actual native fills and complete 5m marked observations; "
            "cumulative, not annualized returns"
        ),
        "qualifications": read(PROTOCOL)["limitations"],
        "independent_forward_validation": False,
        "production_eligible": False,
        "orders_enabled": False,
    }
    save(HERE / "comparison.json", result)
    pd.DataFrame(
        [{k: v for k, v in r.items() if not isinstance(v, (dict, list))} for r in rows]
    ).to_csv(HERE / "comparison.csv", index=False)
    print(json.dumps({"accounts": len(rows), "rankings": rankings}, ensure_ascii=False))


if __name__ == "__main__":
    main()
