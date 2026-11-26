"""Validate the complete daily-to-hourly bridge through the pinned strategy API."""

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent
PARENT = HERE.parent / "head_shoulders_financial_native_20261117_v3"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def main():
    plan = read(PARENT / "plan.json")
    raw_receipt = Path(plan["data_receipts"]["path"])
    assert sha(raw_receipt) == plan["data_receipts"]["sha256"]
    manifest_path = Path(plan["engine_manifest"]["path"])
    assert sha(manifest_path) == plan["engine_manifest"]["sha256"]
    manifest = read(manifest_path)
    vendor = Path(plan["engine_source"])
    assert all(sha(vendor / rel) == digest for rel, digest in manifest["files"].items())
    assert sha(HERE / "source.pdf") == read(HERE / "protocol.json")["source"]["sha256"]
    raw = read(raw_receipt)
    root = Path(plan["datadir"])
    sys.path[:0] = [str(vendor), str(HERE)]
    import freqtrade
    from freqtrade.enums import RunMode

    from Donchian60Tail import Donchian60Tail, daily_features, trail_step

    assert Path(freqtrade.__file__).resolve() == vendor / "freqtrade/__init__.py"
    assert trail_step(1, 100, 99, 105, 0) == (100, "dc60_trail")
    assert trail_step(1, 100, 100, 110, 0) == (110, None)
    assert trail_step(1, 110, 111, 105, 0) == (110, None)
    assert trail_step(-1, 100, 100, 80, 0) == (100, "dc60_trail")
    assert trail_step(-1, 100, 90, 80, 0) == (80, None)
    assert trail_step(-1, 80, 70, 90, 0) == (80, None)
    assert trail_step(-1, 100, 105, 110, 1) == (100, "dc60_opposite")
    config = read(PARENT / "hs40_1000_cost15.json")
    for key in list(config):
        if key.startswith("research_head_shoulders"):
            del config[key]
    config.update(
        strategy="Donchian60Tail",
        runmode=RunMode.BACKTEST,
        research_donchian60=True,
        research_donchian60_daily={},
    )
    strategy = Donchian60Tail(config)
    strategy.bot_start()
    start = pd.Timestamp(plan["native_start"])
    end = pd.Timestamp(plan["end"])
    rows = []
    for pair in plan["pairs"]:
        stem = "futures/" + pair.replace("/", "_").replace(":", "_")
        hourly, daily = (root / f"{stem}-{size}-futures.feather" for size in ("1h", "1d"))
        for path in (hourly, daily):
            assert sha(path) == raw[str(path.relative_to(root))]
        hour = pd.read_feather(hourly)
        day = pd.read_feather(daily)
        source_daily = daily_features(day)
        by_day = hour.groupby(hour.date.dt.floor("D")).close.agg(["count", "last"])
        matched = by_day.reindex(day.date)
        assert matched["count"].eq(24).all(), pair
        assert (matched["last"].to_numpy() == day.close.to_numpy()).all(), pair
        config["research_donchian60_daily"][pair] = {"path": str(daily), "sha256": sha(daily)}
        full_hour = hour.loc[hour.date.ge(start) & hour.date.lt(end)].copy()
        full = strategy.ft_advise_signals(strategy.populate_indicators(full_hour, {"pair": pair}), {"pair": pair})
        cut = pd.Timestamp("2026-01-01T12:00:00Z")
        prefix_hour = hour.loc[hour.date.ge(start) & hour.date.lt(cut)].copy()
        prefix = strategy.ft_advise_signals(strategy.populate_indicators(prefix_hour, {"pair": pair}), {"pair": pair})
        assert prefix.dc60_direction.equals(full.loc[full.date.lt(cut), "dc60_direction"]), pair
        assert len(full) == 14593
        signal = full.loc[full.enter_long.eq(1) | full.enter_short.eq(1)]
        assert signal.date.dt.hour.eq(23).all()
        dates = (signal.date - pd.Timedelta(hours=23)).array
        expected = source_daily.set_index("date").loc[dates].direction.to_numpy()
        assert (expected == signal.dc60_direction.to_numpy()).all()
        assert not (full.enter_long.eq(1) & full.enter_short.eq(1)).any()
        rows.append({
            "pair": pair, "complete_daily_closes": len(day), "signal_rows": len(signal),
            "long": int(signal.enter_long.sum()), "short": int(signal.enter_short.sum()),
            "native_hourly_rows": len(full), "selected_prefix_identical": True,
        })
    result = {
        "passed": True, "utc": datetime.now(UTC).isoformat(), "pair_results": rows,
        "total_source_signal_rows": sum(row["signal_rows"] for row in rows),
        "vendor_files_verified": len(manifest["files"]), "native_istrategy_hook": "ft_advise_signals",
        "daily_receipts": config["research_donchian60_daily"], "trail_fixture_assertions": 7,
        "order_fills_or_accounts": 0, "source_sha256": sha(HERE / "Donchian60Tail.py"),
        "protocol_sha256": sha(HERE / "protocol.json"),
    }
    with (HERE / "preflight.json").open("x") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print(json.dumps({key: result[key] for key in ("passed", "total_source_signal_rows", "vendor_files_verified")}))


if __name__ == "__main__":
    main()
