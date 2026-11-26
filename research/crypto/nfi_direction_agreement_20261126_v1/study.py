"""Align old native NFI trades with pre-existing, lagged direction scores."""

from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt, save


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MODEL = ROOT.with_name("Vibe-Trading") / "agent/research/crypto_tail_decomposition_20261126_v1"
PARENT = HERE.parent / "nfi80_online_baseline_20261125_v1"
SCORE = MODEL / "diagnostics/core_crypto_forecast_direction/2026/factor.csv.gz"
DAY = pd.Timedelta(days=1)


def annotate(trades, ranks):
    rows = []
    for trade in trades:
        pair = trade["pair"]
        opened = pd.Timestamp(trade["open_timestamp"], unit="ms", tz="UTC")
        decision = opened.normalize() - DAY
        assert decision + DAY <= opened and decision < opened
        rank = ranks.at[decision, pair] if decision in ranks.index and pair in ranks else np.nan
        payoff = float(trade["profit_abs"])
        margin = sum(float(row["amount"]) * float(row["safe_price"]) / float(trade["leverage"])
                     for row in trade["orders"] if row["ft_is_entry"])
        assert margin > 0 and np.isfinite(payoff)
        short = trade["is_short"]
        rows.append({
            "pair": pair, "opened": opened.isoformat(), "score_date": decision.isoformat(),
            "short": short, "entry_tag": trade["enter_tag"], "natural": trade["exit_reason"] != "force_exit",
            "rank": float(rank) if np.isfinite(rank) else None,
            "agrees": bool(rank < .5 if short else rank >= .5) if np.isfinite(rank) else None,
            "pnl_usdt": payoff, "entry_margin_usdt": margin, "pnl_per_total_entry_margin": payoff / margin,
            "half": "first" if opened.month <= 6 else "second",
        })
    return rows


def comparison(rows):
    observed = [row for row in rows if row["rank"] is not None and row["natural"]]
    sliced = {
        "all": observed,
        "long": [row for row in observed if not row["short"]],
        "short": [row for row in observed if row["short"]],
        "first_half": [row for row in observed if row["half"] == "first"],
        "second_half": [row for row in observed if row["half"] == "second"],
    }
    results = {}
    for name, group in sliced.items():
        sides = {}
        for label, accepted in (("agrees", True), ("disagrees", False)):
            legs = [row for row in group if row["agrees"] == accepted]
            values = np.asarray([row["pnl_usdt"] for row in legs])
            sides[label] = {"campaigns": len(values)}
            if len(values):
                sides[label].update({
                    "wins_ge_25usdt": int((values >= 25).sum()), "losses_ge_25usdt": int((values <= -25).sum()),
                    "win_rate_ge_25usdt": float((values >= 25).mean()),
                    "loss_rate_ge_25usdt": float((values <= -25).mean()),
                    "median_usdt": float(np.median(values)), "mean_usdt": float(values.mean()),
                    "median_return_on_entry_margin": float(np.median(
                        [row["pnl_per_total_entry_margin"] for row in legs]
                    )),
                })
        results[name] = sides
    return {"all_trade_count": len(rows), "terminal_trades": sum(not row["natural"] for row in rows),
            "scored_natural_trades": len(observed),
            "missing_score_natural_trades": sum(row["rank"] is None and row["natural"] for row in rows),
            "by_slice": results}


def main():
    score = pd.read_csv(SCORE, index_col=0, parse_dates=True, float_precision="round_trip")
    assert score.index.is_unique and score.index.is_monotonic_increasing and str(score.index.tz) == "UTC"
    ranks = score.rank(axis=1, pct=True)
    native = {}
    for cost in (15, 8):
        report = PARENT / f"comparison_{cost}bp.json"
        sample = read(report)["control"]
        assert sample["five_minute_nav_reconciliation"]["passed"]
        archive, = (row for row in sample["sources"] if row["path"].endswith(".zip"))
        native[cost] = {"report": receipt(report), "archive": archive}
    anchor = HERE / "freeze.json"
    freeze = {"protocol": receipt(HERE / "protocol.json"), "analysis": receipt(Path(__file__)),
              "score": receipt(SCORE), "model": receipt(MODEL / "freeze.json"), "sources": native}
    if anchor.exists():
        raise FileExistsError("This attribution has already been frozen")
    save(anchor, freeze)
    output = {}
    for cost, src in native.items():
        stats = stats_from(checked(src["archive"]))
        rows = annotate(stats["trades"], ranks)
        evidence = HERE / f"observed_trades_{cost}bp.json"
        save(evidence, rows)
        output[str(cost)] = {**comparison(rows), "trade_annotation": receipt(evidence)}
    save(HERE / "result.json", {"basis": receipt(anchor), "costs": output,
                              "new_native_accounts": 0, "orders_enabled": False})
    for cost, value in output.items():
        print(cost, value["scored_natural_trades"], value["by_slice"])


if __name__ == "__main__":
    main()
