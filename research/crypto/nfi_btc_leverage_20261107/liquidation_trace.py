"""Observe native buffered levels in an exact replay of the 15-bps 65-day 2x account."""

import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent
NAME = "Native2x"
COST = 15


def main():
    bootstrap = json.loads((HERE / "native_temporal_Native2x.json").read_text())
    sys.path.insert(0, bootstrap["engine_source"])

    from freqtrade.persistence import LocalTrade
    from research.crypto.nfi_btc_leverage_20261107 import study
    from research.crypto.nfi_strength_allocation_20261107.audit import native_trades
    from research.crypto.strategy_comparison_wide_20261105 import run_wide_comparison as native

    path = study.check()["temporal_Native2x"]
    plan = study.read(path)
    (case,) = plan["candidates"]
    native.verify(plan, case)
    base_path = study.OUT / "temporal_Native2x_15bps"
    output = study.OUT / "liquidation_trace_temporal_15bps"
    base_audit = study.read(base_path / "audit.json")
    base, details = native_trades(plan, base_path, case["strategy"], COST)
    if details["archive_sha256"] != base_audit["archive_sha256"]:
        raise ValueError("Reference native archive changed")
    reference = native.checked(base_audit["hourly_equity"])
    native.save(
        study.HERE / "liquidation_trace_registration.json",
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "purpose": "Read native buffered levels omitted by the native archive schema.",
            "original_protocol": study.record(study.PROTOCOL),
            "native_plan": study.record(path),
            "source": study.record(Path(__file__)),
            "reference_archive_sha256": details["archive_sha256"],
            "reference_hourly_equity": base_audit["hourly_equity"],
            "admission": "Require exact native trades and hourly equity parity.",
            "economic_results_already_observed": True,
            "orders_enabled": False,
        },
    )
    original = LocalTrade.set_liquidation_price
    trace = output / "buffered_levels.jsonl"

    def capture(self, value):
        original(self, value)
        row = {
            "pair": self.pair,
            "short": self.is_short,
            "trade_id": self.id,
            "last_filled_utc": self.date_last_filled_utc.isoformat(),
            "margin_usdt": self.stake_amount,
            "entry_price": self.open_rate,
            "quantity": self.amount,
            "leverage": self.leverage,
            "buffered_level": self.liquidation_price,
        }
        with trace.open("a") as f:
            f.write(json.dumps(row, allow_nan=False) + "\n")

    LocalTrade.set_liquidation_price = capture
    try:
        native.run(path, NAME, COST, output)
    finally:
        LocalTrade.set_liquidation_price = original

    replay, audit = native_trades(plan, output, case["strategy"], COST)
    if replay != base:
        raise ValueError("The observational replay changed native trades")
    pd.testing.assert_frame_equal(
        pd.read_parquet(reference), pd.read_parquet(output / "hourly_equity.parquet")
    )
    rows = study.jsonl(trace)
    ids = {row["trade_id"] for row in rows}
    if len(ids) != len(replay) or any(
        row["buffered_level"] is None or row["leverage"] != 2.0 for row in rows
    ):
        raise ValueError("Missing native liquidation levels")
    distances = [
        (row["buffered_level"] / row["entry_price"] - 1) * (1 if row["short"] else -1)
        for row in rows
    ]
    if not all(x > 0 for x in distances):
        raise ValueError("Invalid direction of model buffer relative to entry")
    native.save(
        study.HERE / "liquidation_trace_result.json",
        {
            "trace": study.record(trace),
            "identical_trade_rows": len(replay),
            "identical_hourly_equity": True,
            "replay_archive_sha256": audit["archive_sha256"],
            "buffered_level_events": len(rows),
            "buffer_distance_from_weighted_entry_min": min(distances),
            "buffer_distance_from_weighted_entry_max": max(distances),
            "liquidation_exits": sum(t["exit_reason"] == "liquidation" for t in replay),
        },
    )
    cache = output / "lossless_cache"
    if cache.is_dir() and not cache.is_symlink():
        shutil.rmtree(cache)


if __name__ == "__main__":
    main()
