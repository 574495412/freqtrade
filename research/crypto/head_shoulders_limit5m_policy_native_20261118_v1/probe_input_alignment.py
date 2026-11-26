"""Existing input equality and one source prefix, without an account or return."""

import gc
import hashlib
import json
import socket
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
OWNER = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(name, value):
    with (HERE / name).open("x") as stream:
        json.dump(value, stream, indent=2, default=str, allow_nan=False)
        stream.write("\n")


def verify():
    frozen = json.loads((HERE / "input_probe_freeze.json").read_text())
    for path, digest in frozen["files"].items():
        if sha(path) != digest:
            raise ValueError("Frozen input probe file changed: " + path)
    return frozen


def main():
    started = time.monotonic()
    frozen = verify()
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(OWNER))
    from probe_interfaces import forbidden, resources, verify as verify_interface
    import numpy as np
    import pandas as pd
    from freqtrade.configuration import TimeRange
    from freqtrade.exchange import Exchange
    from freqtrade.optimize.backtesting import Backtesting, DATE_IDX, ENTER_TAG_IDX
    from freqtrade.persistence import Trade
    from freqtrade.wallets import Wallets
    from HeadShouldersLimit5mInputs import FIVE, HOUR, NATIVE_START, NATIVE_END, base_calendar
    from HeadShouldersLimit5mStrategy import HeadShouldersLimit5mStrategy
    from freqtrade.data.dataprovider import DataProvider
    from test_interfaces import config

    resources()
    verify_interface()
    cfg = config()
    paths = frozen["inputs"]
    counts = []
    with patch.object(Backtesting, "__init__", forbidden), patch.object(Backtesting, "start", forbidden), \
         patch.object(Backtesting, "backtest", forbidden), patch.object(Wallets, "__init__", forbidden), \
         patch.object(Exchange, "__init__", forbidden), patch.object(socket.socket, "connect", forbidden):
        for item in paths:
            base = pd.read_feather(item["five_minute"])
            base = base.loc[(base.date >= NATIVE_START - 800 * FIVE) & (base.date < NATIVE_END)].copy()
            base_calendar(base)
            if len(base) != 175904:
                raise ValueError("Unexpected full-window native rows: " + item["pair"])
            groups = base.groupby(base.date.dt.floor("h"))
            aggregate = groups.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                   close=("close", "last"), volume=("volume", "sum"), rows=("date", "size"))
            aggregate = aggregate.loc[aggregate.rows == 12]
            hourly = pd.read_feather(item["one_hour"]).set_index("date")
            match = hourly.loc[aggregate.index]
            columns = ["open", "high", "low", "close"]
            difference = aggregate[columns].to_numpy() != match[columns].to_numpy()
            volume_diff = np.abs(aggregate.volume.to_numpy() - match.volume.to_numpy())
            volume_equal = np.isclose(aggregate.volume, match.volume, rtol=1e-12, atol=1e-8)
            row = {"pair": item["pair"], "native_5m_rows": len(base), "complete_hours": len(aggregate),
                   "price_fields_compared_exact": int(difference.size),
                   "price_mismatches": int(difference.sum()), "volume_mismatches": int((~volume_equal).sum()),
                   "max_volume_float_sum_difference": float(volume_diff.max()),
                   "excluded_partial_warmup_hours": int((groups.size() != 12).sum())}
            counts.append(row)
            if row["price_mismatches"] or row["volume_mismatches"]:
                save("input_alignment_failure_details.json", {"records": counts, "failed_pair": item["pair"]})
                raise ValueError("5m versus original1h source input mismatch")
            del base, aggregate, hourly, match, groups
            gc.collect()
            resources()

        # Frozen, return-independent choice: first executable BTC source event after native start.
        catalog = pd.read_parquet(frozen["source_catalog"])
        pair = cfg["exchange"]["pair_whitelist"][0]
        reference = catalog.loc[catalog.pair.eq(pair)].copy()
        executable = reference.loc[(reference.entry_available > NATIVE_START) & ~reference.self_terminal_at_confirmation]
        chosen = executable.sort_values("entry_available").iloc[0]
        endpoint = chosen.entry_available + HOUR
        item = next(row for row in paths if row["pair"] == pair)
        base = pd.read_feather(item["five_minute"])
        base = base.loc[(base.date >= NATIVE_START - 800 * FIVE) & (base.date < endpoint)].reset_index(drop=True)
        strategy = HeadShouldersLimit5mStrategy(cfg)
        strategy.dp = DataProvider(cfg, exchange=None)
        strategy.ft_bot_start()
        analyzed = strategy.advise_all_indicators({pair: base})[pair]
        records = strategy._pair_events[pair]
        expected = reference.loc[reference.confirmation_bar_open <= endpoint - HOUR]
        if {row["event_id"] for row in records} != set(expected.event_id):
            raise ValueError("Source prefix changed original geometric event identities")
        context = SimpleNamespace(
            strategy=strategy, dataprovider=strategy.dp, timeframe="5m", config=cfg, required_startup=800,
            timerange=TimeRange.parse_timerange(cfg["timerange"]),
            _set_progress_step=lambda *args: None, check_abort=lambda: None, _increment_progress=lambda: None,
        )
        converted = Backtesting._get_ohlcv_as_lists(context, {pair: analyzed})[pair]
        signals = [row for row in converted if row[ENTER_TAG_IDX] == chosen.event_id]
        if len(signals) != 1 or pd.Timestamp(signals[0][DATE_IDX]) != chosen.entry_available:
            raise ValueError("Actual source event did not map to exact initial native row")
        now = chosen.entry_available
        strategy.dp._set_dataframe_max_index(pair, int(base.date.searchsorted(now)))
        strategy.dp._set_dataframe_max_date(now.to_pydatetime())
        original_use_db = Trade.use_db
        try:
            Trade.use_db = False
            accepted = strategy.confirm_trade_entry(pair, "limit", 1.0, 100.0, "GTC", now, chosen.event_id, chosen.side)
        finally:
            Trade.use_db = original_use_db
        if not accepted or strategy.callback_errors:
            raise ValueError("Actual source prefix interface failed or callback errors remain")
        prefix = {"pair": pair, "selection": "first original executable BTC event after native_start, no outcomes",
                  "event_id": chosen.event_id, "entry_available": str(now), "native_prefix_rows": len(base),
                  "identical_source_event_ids": len(records), "exact_native_signal_clock": True,
                  "LIMIT_entry_confirmed_interface_only": accepted, "callback_errors": strategy.callback_errors}
    verify()
    verify_interface()
    save("input_alignment_validation.json", {"passed": True, "finished_at": datetime.now(UTC).isoformat(),
        "seconds": time.monotonic() - started, "pairs": len(counts), "records": counts,
        "price_rule": "bit-value numeric equality, no OHLC price tolerance",
        "volume_rule": "float sum only: rtol1e-12, atol1e-8; not an execution quantity rule",
        "source_prefix": prefix, "financial_accounts": 0, "orders_filled": 0,
        "economic_result": "UNKNOWN", "all40_native_signal_lifecycle": "NOT_RUN",
        "input_probe_freeze_sha256": sha(HERE / "input_probe_freeze.json"), "resources": resources()})
    print(json.dumps({"passed": True, "pairs": len(counts), "source_prefix": prefix, "seconds": time.monotonic() - started}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        save("input_probe_failure.json", {"passed": False, "error": str(error), "traceback": traceback.format_exc(),
                                         "at": datetime.now(UTC).isoformat()})
        raise
