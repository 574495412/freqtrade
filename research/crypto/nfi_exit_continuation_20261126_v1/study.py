"""Measure directional price follow-through after profitable native NFI exits."""

from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt, save


HERE = Path(__file__).resolve().parent
PARENT = HERE.parent / "nfi80_online_baseline_20261125_v1"
HORIZONS = (4, 24)


def market_price(pair, plan, receipts):
    stem = pair.replace("/", "_").replace(":", "_")
    name = f"futures/{stem}-5m-futures.feather"
    source = {"path": str(Path(plan["datadir"]) / name), "sha256": receipts[name]}
    path = checked(source)
    raw = pd.read_feather(path, columns=["date", "open", "volume"]).set_index("date")
    assert raw.index.is_unique and raw.index.is_monotonic_increasing
    return raw.open.where(raw.volume > 0), source


def campaigns(trades, plan, receipts, price_data, sources):
    observed = []
    counts = {"not_profitable_natural": 0, "terminal": 0, "missing_or_late": 0}
    cutoff = pd.Timestamp(plan["comparison_end"])
    for trade in trades:
        if trade["exit_reason"] == "force_exit":
            counts["terminal"] += 1
            continue
        if trade["profit_abs"] <= 0:
            counts["not_profitable_natural"] += 1
            continue
        time = pd.Timestamp(trade["close_timestamp"], unit="ms", tz="UTC")
        start = time.floor("5min") + pd.Timedelta(minutes=5)
        ends = [start + pd.Timedelta(hours=size) for size in HORIZONS]
        pair = trade["pair"]
        if pair not in price_data:
            price_data[pair], sources[pair] = market_price(pair, plan, receipts)
        prices = price_data[pair].reindex([start, *ends]).to_numpy()
        if ends[-1] > cutoff or not (np.isfinite(prices).all() and (prices > 0).all()):
            counts["missing_or_late"] += 1
            continue
        original_pair_return = prices[1:] / prices[0] - 1
        directional = original_pair_return * (-1 if trade["is_short"] else 1)
        observed.append({
            "pair": pair, "entry_tag": trade["enter_tag"], "close_time": time.isoformat(),
            "anchor": start.isoformat(), "short": bool(trade["is_short"]),
            "realized_native_profit_usdt": trade["profit_abs"],
            "signed_move_4h": float(directional[0]), "signed_move_24h": float(directional[1]),
            "half": "first" if time.month <= 6 else "second",
        })
    return observed, counts


def profile(rows):
    categories = {
        "all": rows,
        "long": [row for row in rows if not row["short"]],
        "short": [row for row in rows if row["short"]],
        "first_half": [row for row in rows if row["half"] == "first"],
        "second_half": [row for row in rows if row["half"] == "second"],
    }
    output = {}
    for role, table in categories.items():
        output[role] = {}
        for hours in HORIZONS:
            values = np.asarray([row[f"signed_move_{hours}h"] for row in table])
            output[role][str(hours)] = {"campaigns": len(values)}
            if len(values):
                output[role][str(hours)].update({
                    "mean": float(values.mean()), "median": float(np.median(values)),
                    "positive": float((values > 0).mean()), "up_5pct": float((values >= .05).mean()),
                    "down_5pct": float((values <= -.05).mean()),
                })
    return output


def main():
    parent = PARENT / "plan.json"
    plan = read(parent)
    receipts = read(checked(plan["data_receipts"]))
    archives = {}
    for cost in (15, 8):
        witness = read(PARENT / f"comparison_{cost}bp.json")["control"]
        assert witness["five_minute_nav_reconciliation"]["passed"]
        archives[cost], = [row for row in witness["sources"] if row["path"].endswith(".zip")]
    anchor = HERE / "freeze.json"
    if anchor.exists():
        raise FileExistsError("Attribution is already frozen")
    save(anchor, {"parent_plan": receipt(parent), "protocol": receipt(HERE / "protocol.json"),
                  "implementation": receipt(Path(__file__)), "native_archives": archives})
    output, prices, sources = {}, {}, {}
    for cost, archive in archives.items():
        rows, excluded = campaigns(stats_from(checked(archive))["trades"], plan, receipts, prices, sources)
        per_trade = HERE / f"price_labels_{cost}bp.json"
        save(per_trade, rows)
        output[str(cost)] = {"checked_campaigns": len(rows), "excluded": excluded,
                             "market_moves": profile(rows), "per_trade": receipt(per_trade)}
    save(HERE / "result.json", {"freeze": receipt(anchor), "costs": output,
                              "price_inputs": sources, "account_runs": 0, "orders_enabled": False})
    print({cost: {"observed": value["checked_campaigns"], "all": value["market_moves"]["all"]}
           for cost, value in output.items()})


if __name__ == "__main__":
    main()
