"""Register the longer account's immutable inputs without copying year tapes."""

from pathlib import Path

import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .continuous_seams import INTERVALS, open_inputs
from .inputs import HERE, OUT


ROOT = OUT / "continuous_wallet_inputs_20261126"


def register():
    seams = receipt(HERE / "continuous_native_input_seams.json")
    bnt = receipt(HERE / "continuous_bnt_coverage.json")
    transitions = read(checked(seams))["market_transition_probe"]
    for seam in transitions.values():
        assert not seam["market_rules"]["field_differences"] and not seam["market_rules"]["leverage_tier_differences"]
        assert not seam["previous_day_pool_missing_later"]
        assert all(t["value_differences"] == 0 for t in seam["by_series"].values())
        assert all(row["kind"] in {"5m-mark", "1h-mark", "1h-funding_rate"}
                   and not row["overlap_rows"] and not row["missing_later_interval_rows"]
                   and row["first_later_time"] is not None for row in seam["input_exceptions"])
    refs, plans = open_inputs()
    assert refs == read(checked(seams))["plans"] == read(checked(bnt))["input_plans"]
    days, streams = {}, {}
    for year, plan in plans.items():
        source = read(checked(plan["historical_schedule"]))
        assert set(days).isdisjoint(source["days"])
        days.update(source["days"])
        for relative, digest in read(checked(plan["data_receipts"])).items():
            path = Path(plan["datadir"]) / relative
            assert path.is_file()
            blocks = streams.setdefault(relative, [])
            blocks.append({"source_year": year, "path": str(path), "sha256": digest,
                           "start_inclusive": plan["start"] if blocks else None, "stop_exclusive": plan["end"]})
    extra = read(checked(bnt))
    later = plans["2026"]
    assert extra["window"] == [later["start"], later["end"]] and set(extra["files"]) == set(INTERVALS)
    assert not extra["recent_pairlist_contains_pair"]
    for metadata in extra["files"].values():
        path = checked(metadata)
        relative = f"futures/{path.name}"
        assert streams[relative][-1]["source_year"] == "2025"
        streams[relative].append({"source_year": "2026_BNT_carry", "path": str(path),
                                  "sha256": metadata["sha256"], "start_inclusive": later["start"],
                                  "stop_exclusive": later["end"]})
    universe = sorted(set().union(*(set(plan["pairs"]) for plan in plans.values())))
    expected = {
        f"futures/{pair.replace('/', '_').replace(':', '_')}-{interval}.feather"
        for pair in universe for interval in INTERVALS
    }
    assert set(streams) == expected
    for daily in days.values():
        assert len(set(daily["pairs"])) == 80 and set(daily["pairs"]).issubset(universe)
    start, stop = map(pd.Timestamp, (plans["2024"]["start"], later["end"]))
    grid = pd.date_range(start, stop.floor("D"), freq="D")
    assert list(days) == grid.strftime("%Y-%m-%d").tolist()
    for relative, entries in streams.items():
        assert all(a["stop_exclusive"] == b["start_inclusive"] for a, b in zip(entries, entries[1:]))
        assert entries[-1]["stop_exclusive"] == later["end"], relative
    # The 2025 market descriptor already covers exactly the three-year union.
    # The seam audit verifies the required contract and leverage fields on
    # all shared symbols against the other two descriptors.
    market = plans["2025"]["market_snapshot"]
    descriptor = read(checked(market))
    assert set(descriptor["markets"]) == set(universe) == set(descriptor["leverage_tiers"])
    ROOT.mkdir(exist_ok=True)
    calendar = write_once(ROOT / "schedule.json", {"days": days, "union": universe})
    registry = write_once(ROOT / "shards.json", {
        "plans": refs, "engine_commit": later["engine_commit"], "market_snapshot": market,
        "schedule": calendar, "seams": seams, "bnt_continuation": bnt,
        "files": streams, "first_time": start.isoformat(), "stop_exclusive": stop.isoformat(),
        "daily_decisions": len(days),
        "five_minute_marks_expected": len(pd.date_range(start, stop, freq="5min", inclusive="left")),
        "registration_source": receipt(Path(__file__)), "continuous_wallets_completed": 0,
        "orders_enabled": False,
    })
    print(f"Registered {len(days)} days and {len(streams)} logical native feeds at {registry['path']}", flush=True)
    return registry


if __name__ == "__main__":
    register()
