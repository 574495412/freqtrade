"""Partition native archived cash outcomes by the frozen pre-entry signal breadth."""

from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import pandas as pd

from research.crypto.nfi_signal_cluster_20261107 import study
from research.crypto.nfi_strength_allocation_20261107.audit import native_trades, read
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked, save, sha


def buckets(trades, signals):
    totals = defaultdict(lambda: [0, Decimal(0), Decimal(0)])
    for trade in trades:
        first = next(order for order in trade["orders"] if order["ft_is_entry"])
        prior = pd.to_datetime(trade["open_timestamp"] - 300_000, unit="ms", utc=True)
        side = "short" if trade["is_short"] else "long"
        count = signals[prior, side]
        if not count:
            raise ValueError("Archived native trade has no prior-bar source signal")
        group = "cluster" if count > 1 else "single"
        status = "forced" if trade["exit_reason"] == "force_exit" else "natural"
        row = totals[f"{group}_{status}"]
        row[0] += 1
        row[1] += trade["profit_abs"]
        row[2] += first["amount"] * first["safe_price"]
    return {
        key: {"positions": n, "net_pnl_usdt": float(net), "first_notional_usdt": float(gross)}
        for key, (n, net, gross) in sorted(totals.items())
    }


def main():
    report = read(study.RESULT)
    if report["registration_sha256"] != sha(study.POLICY):
        raise ValueError("Study results differ from registration")
    plan = read(checked(read(study.POLICY)["plans"]["SignalCluster"]))
    original = read(study.leverage.check()["2024_Native2x"])
    original_accounts = read(study.leverage.RESULT)["stages"]["2024"]["accounts"]
    output = {}
    for cost in study.COSTS:
        key = f"2024_SignalCluster_{cost}bps"
        account = report["accounts"][key]
        signal_rows = Counter()
        for receipt in account["signal_feeds"].values():
            df = pd.read_parquet(checked(receipt))
            signal_rows.update(zip(df.date, df.side, strict=True))
        base_name = f"2024_Native2x_{cost}bps"
        base_audit = original_accounts[base_name]
        base_path = checked(base_audit["hourly_equity"]).parent
        base, base_detail = native_trades(original, base_path, "NostalgiaForInfinityX8", cost)
        variant, detail = native_trades(plan, study.OUT / key, "SignalCluster", cost)
        if (
            base_detail["archive_sha256"] != base_audit["archive_sha256"]
            or detail["archive_sha256"] != account["archive_sha256"]
        ):
            raise ValueError("Native source archive changed")
        output[str(cost)] = {
            "native_2x": buckets(base, signal_rows),
            "cluster_2x": buckets(variant, signal_rows),
        }
    save(
        Path(__file__).with_name("attribution.json"),
        {
            "result_sha256": sha(study.RESULT),
            "source_sha256": sha(Path(__file__)),
            "scope": "Post-result attribution on each native account path.",
            "accounts": output,
        },
    )


if __name__ == "__main__":
    main()
