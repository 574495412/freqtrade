"""Real native constructor, DataProvider, signal clocks and synthetic callbacks."""

import gc
import hashlib
import json
import resource
import shutil
import socket
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).parent.resolve()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, indent=2, default=str, allow_nan=False)
        stream.write("\n")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def guard():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss = peak if sys.platform == "darwin" else peak * 1024
    free = shutil.disk_usage(HERE).free
    require(rss < 2 * 1024**3, "Peak RSS exceeds 2 GiB")
    require(free >= 1024**3, "Free disk below 1 GiB")
    return {"peak_rss_bytes": rss, "free_disk_bytes": free}


def verify_frozen(protocol):
    freeze = json.loads((HERE / "implementation_freeze.json").read_text())
    for path, digest in freeze["files"].items():
        require(sha(path) == digest, "Frozen implementation differs: " + path)
    for key in [
        "source_manifest",
        "source_protocol",
        "source_loader",
        "engine_manifest",
        "market_snapshot",
    ]:
        record = protocol[key]
        require(sha(record["path"]) == record["sha256"], "Frozen reference differs: " + key)
    source_manifest = json.loads(Path(protocol["source_manifest"]["path"]).read_text())
    for record in source_manifest["files"]:
        require(sha(record["path"]) == record["sha256"], "Sealed v1 artifact changed")
    engine = Path(protocol["engine_source"])
    manifest = json.loads(Path(protocol["engine_manifest"]["path"]).read_text())
    for relative, digest in manifest["files"].items():
        require(sha(engine / relative) == digest, "Official engine file differs: " + relative)
    return len(manifest["files"]), source_manifest["file_count"]


def main():
    guard()
    protocol = json.loads((HERE / "protocol.json").read_text())
    engine_files, source_files = verify_frozen(protocol)
    for record in protocol["history_inputs"] + protocol["native_auxiliary_inputs"]:
        require(sha(record["path"]) == record["sha256"], "Frozen market input differs")
    engine = Path(protocol["engine_source"])
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(engine))
    import numpy as np
    import pandas as pd
    from test_callbacks import run_callback_fixtures

    import freqtrade
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize.backtesting import (
        DATE_IDX,
        ENTER_TAG_IDX,
        LONG_IDX,
        SHORT_IDX,
        Backtesting,
    )
    from freqtrade.persistence import LocalTrade
    from freqtrade.resolvers import ExchangeResolver

    require(
        Path(freqtrade.__file__).resolve() == engine / "freqtrade/__init__.py", "Wrong native owner"
    )
    output = HERE / "native_probe"
    output.mkdir(exist_ok=False)
    cfg = setup_optimize_configuration(
        {
            "config": [str(HERE / "native_config.json")],
            "datadir": json.loads((HERE / "native_config.json").read_text())["datadir"],
        },
        RunMode.BACKTEST,
    )
    exchange = ExchangeResolver.load_exchange(cfg, validate=False, load_leverage_tiers=False)
    snapshot = json.loads(Path(protocol["market_snapshot"]["path"]).read_text())
    exchange._markets = snapshot["markets"]
    exchange._leverage_tiers = snapshot["leverage_tiers"]
    exchange._api.set_markets(snapshot["markets"], snapshot["currencies"])
    exchange._api_async.set_markets(snapshot["markets"], snapshot["currencies"])
    exchange.validate_config(cfg)

    def no_network(*args, **kwargs):
        raise RuntimeError("Frozen interface probe attempted network")

    socket.socket.connect = no_network
    account = None
    stage = "native_constructor"
    try:
        account = Backtesting(cfg, exchange=exchange)
        strategy = account.strategylist[0]
        account._set_strategy(strategy)
        require(
            account.pairlists.whitelist == protocol["ordered_pairs"], "Native pool order differs"
        )
        require(
            strategy.startup_candle_count == 0 and strategy.timeframe == "1h",
            "Native warmup changed",
        )
        require(
            strategy.stoploss == -0.99 and strategy.minimal_roi == {},
            "Native risk contract differs",
        )
        require(
            not strategy.trailing_stop and not strategy.position_adjustment_enable,
            "DCA/trailing enabled",
        )
        require(strategy.use_exit_signal and not strategy.exit_profit_only, "Custom exit disabled")
        require(
            account.timeframe_detail == "5m" and not account._position_stacking,
            "Native timing differs",
        )
        stage = "synthetic_native_callbacks"
        callback_receipt = run_callback_fixtures(type(strategy), cfg, account.dataprovider)
        save(output / "callback_validation.json", callback_receipt)
        stage = "native_data_all40"
        data, _ = account.load_bt_data()
        require(list(data) == protocol["ordered_pairs"], "Native data order differs")
        require(
            list(account.detail_data) == protocol["ordered_pairs"], "Native detail data differs"
        )
        require(
            set(account.futures_data) == set(protocol["ordered_pairs"]),
            "Native funding/mark coverage differs",
        )
        guard()
        receipts, all_records = [], []
        for pair_order, pair in enumerate(protocol["ordered_pairs"]):
            stage = "native_indicators_and_signals:" + pair
            frame = data[pair]
            require(
                frame.date.iloc[0] == pd.Timestamp(protocol["native_start"]), "Native start differs"
            )
            require(
                frame.date.iloc[-1] == pd.Timestamp("2026-08-31T23:00:00Z"), "Native end differs"
            )
            analyzed = strategy.advise_all_indicators({pair: frame})[pair]
            analyzed = strategy.ft_advise_signals(analyzed, {"pair": pair})
            records = strategy._pair_events[pair]
            event_lookup = {record["event_id"]: record for record in records}
            active = analyzed.hs_event_id.ne("") & ~analyzed.hs_self_terminal
            np.testing.assert_array_equal(
                analyzed.enter_long, (active & analyzed.hs_direction.eq(1)).astype(int)
            )
            np.testing.assert_array_equal(
                analyzed.enter_short, (active & analyzed.hs_direction.eq(-1)).astype(int)
            )
            require(
                not analyzed.exit_long.any() and not analyzed.exit_short.any(),
                "Unexpected vectorized exits",
            )
            require(
                analyzed.loc[analyzed.hs_self_terminal, ["enter_long", "enter_short"]].sum().sum()
                == 0,
                "Zero-duration event emitted an entry",
            )
            # Actual native signal conversion and DP cache, without calling backtest/start.
            rows = account._get_ohlcv_as_lists({pair: analyzed})[pair]
            shifted = [row for row in rows if row[LONG_IDX] or row[SHORT_IDX]]
            expected = analyzed.loc[active & (analyzed.date < analyzed.date.iloc[-1])]
            require(len(shifted) == len(expected), "Native shifted entry count differs")
            confirmations = []
            for row in shifted:
                tag = row[ENTER_TAG_IDX]
                event = event_lookup[tag]
                now = row[DATE_IDX]
                require(
                    now == event["entry_available"],
                    "Native signal/tag shifted more or less than once",
                )
                require(bool(row[LONG_IDX]) == event["inverted"], "Native direction differs")
                position = int(analyzed.date.searchsorted(event["confirmation_bar_open"]))
                account.dataprovider._set_dataframe_max_index(pair, position + 1)
                account.dataprovider._set_dataframe_max_date(now)
                allowed = strategy.confirm_trade_entry(
                    pair,
                    "market",
                    1,
                    float(analyzed.close.iloc[position]),
                    "GTC",
                    now,
                    tag,
                    event["side"],
                )
                require(allowed, "No-position source entry failed native callback admission")
                confirmations.append({"event_id": tag, "entry_clock": str(now), "allowed": allowed})
            for event in records:
                location = (
                    "COLD_HISTORY_ONLY"
                    if event["confirmation_bar_open"] < frame.date.iloc[0]
                    else "FINAL_BAR_NO_NEXT_NATIVE_ROW"
                    if event["entry_available"] > frame.date.iloc[-1]
                    else "CONFIRMATION_ROW_AVAILABLE"
                )
                status = (
                    "MASKED_SELF_TERMINAL_KNOWN_FLAT"
                    if event["self_terminal_at_confirmation"]
                    else location
                )
                all_records.append(
                    {
                        "pair_order": pair_order,
                        **event,
                        "policy_event_status": status,
                        "native_location": location,
                    }
                )
            detail = account.detail_data[pair]
            receipts.append(
                {
                    "pair": pair,
                    "pair_order": pair_order,
                    "native_1h_rows": len(frame),
                    "native_5m_rows": len(detail),
                    "funding_mark_combined_rows": len(account.futures_data[pair]),
                    "source_events": len(records),
                    "self_terminal_events": sum(
                        e["self_terminal_at_confirmation"] for e in records
                    ),
                    "native_shifted_entry_masks": len(shifted),
                    "entry_confirmations": confirmations,
                    "signals_are_not_fills": True,
                }
            )
            del rows, shifted, analyzed
            gc.collect()
            resources = guard()
            print(
                json.dumps(
                    {
                        "pair": pair,
                        "completed_pairs": len(receipts),
                        "source_events": len(records),
                        "native_entry_masks": len(confirmations),
                        "resources": resources,
                    }
                ),
                flush=True,
            )
        require(not strategy.callback_errors, "Real interface callback error")
        pd.DataFrame(all_records).to_parquet(output / "policy_event_catalog.parquet", index=False)
        save(
            output / "policy_audit.json",
            {
                "real_entry_callback_audit": strategy.policy_audit,
                "real_callback_errors": strategy.callback_errors,
                "no_position_real_probe_overlap_count": 0,
                "synthetic_overlap_checks": "callback_validation.json",
            },
        )
        require(
            not LocalTrade.bt_trades and not LocalTrade.bt_trades_open,
            "Detached trades leaked into native state",
        )
        require(
            account.trade_id_counter == 0 and account.order_id_counter == 0,
            "Native order/trade created",
        )
        verify_frozen(protocol)
        receipt = {
            "completed_at": datetime.now(UTC).isoformat(),
            "passed": True,
            "actual_native_constructor": True,
            "exchange_validate_config_called": True,
            "actual_native_data_provider_and_signal_shift": True,
            "all_40_pairs_in_original_order": True,
            "source_recognition_computed_through_native_indicators": True,
            "v1_source_event_parquet_used_as_signal_cache": False,
            "source_manifest_unchanged_files": source_files,
            "official_engine_unchanged_files": engine_files,
            "source_events": len(all_records),
            "self_terminal_events_retained": sum(
                r["self_terminal_at_confirmation"] for r in all_records
            ),
            "native_shifted_entry_masks": sum(r["native_shifted_entry_masks"] for r in receipts),
            "records": receipts,
            "callback_validation": callback_receipt,
            "event_catalog": {
                "path": str(output / "policy_event_catalog.parquet"),
                "sha256": sha(output / "policy_event_catalog.parquet"),
            },
            "fixed_public_market_snapshot_only": True,
            "dated_historical_market_rules_certified": False,
            "native_tick_history_is_not_certified_or_used_for_economic_claims": True,
            "financial_start_called": False,
            "new_native_financial_accounts": 0,
            "actual_fills": 0,
            "financial_performance_proven": False,
            "orders_enabled": False,
            "goal_complete": False,
            "resources": guard(),
        }
        save(output / "admission.json", receipt)
        print("HEAD_SHOULDERS_NATIVE_INTERFACE_PASS", flush=True)
    except Exception as error:
        save(
            output / "failure.json",
            {
                "stage": stage,
                "error_type": type(error).__name__,
                "error": str(error),
                "traceback": traceback.format_exc(),
                "financial_start_called": False,
                "native_financial_accounts": 0,
            },
        )
        raise
    finally:
        if account is not None:
            account.cleanup()
        exchange.close()


if __name__ == "__main__":
    main()
