"""Use the pinned Freqtrade startup trim on the one locally archived listing."""

import json
from pathlib import Path
import sys

import pandas as pd


HERE = Path(__file__).resolve().parent


def main():
    initial = json.loads((HERE / "recent_two_year_15bp_12_verified.json").read_text())["account"]
    initial_plan = json.loads(Path(initial["registered_plan"]["path"]).read_text())
    vendor = Path(initial_plan["engine_source"])
    sys.path.insert(0, str(vendor))
    from freqtrade.configuration.timerange import TimeRange
    from freqtrade.data.converter import trim_dataframe
    from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

    from .accounts import write_once
    from .continuous_native_inputs import IndependentShardRead

    plan = read(checked(initial["registered_plan"]))
    config = read(checked(initial["effective_config"]))
    source = receipt(HERE / "listing_sleeve_capacity.json")
    candidates = read(checked(source))["candidates"]
    known, = [row for row in candidates if row["native_pair"]]
    clock = pd.Timestamp(known["short_order_clock"])
    candle = IndependentShardRead(plan["data_receipts"])(known["native_pair"], "5m-futures")
    assert candle.date.is_unique and candle.date.is_monotonic_increasing
    launch = pd.Timestamp(known["first_trade_clock"])
    assert candle.loc[candle.volume.gt(0), "date"].iloc[0] == launch
    assert launch == candle.date.iloc[0] and clock in set(candle.date)
    native_source = Path(trim_dataframe.__code__.co_filename).resolve()
    assert native_source.is_relative_to(vendor)
    relative = native_source.relative_to(vendor).as_posix()
    digest = read(checked(plan["engine_manifest"]))["files"][relative]
    checked({"path": str(native_source), "sha256": digest})
    limit = config["startup_candle_count"]
    after_trim = trim_dataframe(candle, TimeRange.parse_timerange(config["timerange"]), startup_candles=limit)
    assert after_trim.date.iloc[0] > clock and clock not in set(after_trim.date)
    output = {"source": receipt(Path(__file__)), "capacity_audit": source, "pair": known["native_pair"],
              "archived_native_engine_commit": plan["engine_commit"], "native_trim": receipt(native_source),
              "native_startup_candles": limit, "listing_first_trade": launch.isoformat(), "candidate_clock": clock.isoformat(),
              "completed_native_rows_before_candidate_order": int(candle.date.lt(clock).sum()),
              "first_row_kept_by_native_trim": after_trim.date.iloc[0].isoformat(),
              "no_early_row_after_trim": True, "orders_enabled": False}
    write_once(HERE / "listing_startup_witness.json", output)
    print(output["pair"], output["completed_native_rows_before_candidate_order"], limit, output["first_row_kept_by_native_trim"])


if __name__ == "__main__":
    main()
