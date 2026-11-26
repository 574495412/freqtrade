"""Read-only actual-call tracing; no strategy, order, wallet or clock simulation."""

import hashlib
import inspect
import json
import math
import time
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

import numpy as np
import pandas as pd

from research.crypto.nfi40_frame_cache_native_20261117_v1.frame_storage_v3 import semantic_digest


CALLBACKS = (
    "bot_loop_start",
    "confirm_trade_entry",
    "custom_exit",
    "adjust_trade_position",
    "order_filled",
    "custom_stake_amount",
    "leverage",
    "confirm_trade_exit",
    "custom_entry_price",
    "custom_exit_price",
    "adjust_order_price",
    "check_entry_timeout",
    "check_exit_timeout",
)


def typed(value):
    """Deterministic semantic encoding; fail closed instead of repr(object/address)."""
    if isinstance(value, Enum):
        return {"enum": type(value).__qualname__, "value": typed(value.value)}
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, np.generic):
        return {"numpy_dtype": str(value.dtype), "value": typed(value.item())}
    if isinstance(value, float):
        return {"float": value.hex() if math.isfinite(value) else repr(value)}
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return {"datetime": value.isoformat()}
    if hasattr(value, "_asdict"):
        return typed(value._asdict())
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Trace dictionary keys must be strings")
        return {key: typed(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [typed(item) for item in value]
    # These are original native public serialization APIs, called read-only.
    if hasattr(value, "to_json") and hasattr(value, "entry_side"):
        fields = {key for cls in type(value).__mro__ for key in getattr(cls, "__annotations__", {})}
        fields = sorted(
            key for key in fields if not key.startswith("bt_") and key not in ("use_db", "orders")
        )
        return {
            "native_trade": typed(value.to_json(minified=False)),
            "raw_trade_fields": typed({key: getattr(value, key) for key in fields}),
            "all_orders": [typed(order) for order in value.orders],
        }
    if hasattr(value, "to_json") and hasattr(value, "ft_order_side"):
        trade = getattr(value, "_trade_bt", None)
        if trade is None:
            raise ValueError("Native order trace lacks its native trade reference")
        # Public JSON rounds some amounts/averages. The raw mapped fields retain
        # exact floats and every order timestamp, including pending/canceled ones.
        raw = {column.key: getattr(value, column.key) for column in value.__table__.columns}
        return {
            "native_order": typed(value.to_json(trade.entry_side, minified=False)),
            "raw_order_fields": typed(raw),
        }
    raise TypeError("Unsupported trace value type: " + type(value).__qualname__)


def stable_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def filled_kind(trade, order):
    """Classify the actual native order_filled callback at its owner-defined stage."""
    if order.ft_is_open or order.status != "closed" or not order.filled:
        raise ValueError("order_filled callback did not contain a filled native order")
    if order.ft_order_side == trade.entry_side:
        return "entry" if trade.nr_of_successful_entries == 1 else "position_add"
    # _try_close_open_order calls this before _process_exit_order recalc/close.
    return "full_exit" if order.safe_amount_after_fee == trade.amount else "partial_exit"


class Capture:
    def __init__(self, output, state_reader):
        self.output = output
        self.state_reader = state_reader
        self.phase = "setup"
        self.clock = None
        self.stack = []
        self.records = {
            name: []
            for name in (
                "native_signal_records",
                "dp_read_records",
                "callback_records",
                "order_lifecycle_records",
                "hourly_wallet_records",
                "native_trade_records",
                "state_records",
                "cache_clear_records",
                "cleanup_records",
            )
        }
        self.errors = []
        self.elapsed = {
            "trace_validation_seconds": 0.0,
            "native_dp_read_seconds": 0.0,
            "native_dp_assignment_seconds": 0.0,
            "native_signal_seconds": 0.0,
        }
        self.refresh = {}
        self.last_state_hash = None
        self.event_sequence = 0
        self.stream = (output / "actual_native_trace.jsonl").open("x")

    def append(self, section, record):
        self.event_sequence += 1
        record = {"trace_sequence": self.event_sequence, **record}
        # Full exact semantic payload stays in the append-only trace. Retain only
        # its digest and coverage fields in RAM; callbacks can contain many orders.
        summary = {
            key: record[key]
            for key in ("trace_sequence", "kind", "filled", "callback", "phase")
            if key in record
        }
        summary["semantic_sha256"] = stable_hash(record)
        self.records[section].append(summary)
        self.stream.write(
            json.dumps({"section": section, **record}, sort_keys=True, allow_nan=False) + "\n"
        )

    def state(self, location):
        value = typed(self.state_reader())
        digest = stable_hash(value)
        if digest != self.last_state_hash:
            self.append(
                "state_records",
                {"at": typed(self.clock), "location": location, "sha256": digest, "state": value},
            )
            self.last_state_hash = digest

    def error(self, where, error):
        self.errors.append(
            {
                "where": where,
                "type": type(error).__name__,
                "error": str(error),
                "clock": typed(self.clock),
            }
        )
        self.stream.flush()

    def fail_if_errors(self):
        if self.errors:
            raise RuntimeError(
                "Actual native callback/storage trace error: " + json.dumps(self.errors)
            )

    def install_provider(self, provider, variant):
        original_get = provider.get_analyzed_dataframe
        original_set = provider._set_cached_df
        original_clear = provider.clear_cache

        def assign(pair, timeframe, dataframe, candle_type):
            before = time.perf_counter()
            original_set(pair, timeframe, dataframe, candle_type)
            self.elapsed["native_dp_assignment_seconds"] += time.perf_counter() - before
            # The just-assigned key is already most-recent in both mappings.
            _, refreshed = provider._DataProvider__cached_pairs[(pair, timeframe, candle_type)]
            self.refresh[(pair, timeframe)] = refreshed

        def get(pair, timeframe):
            before = time.perf_counter()
            frame, refreshed = original_get(pair, timeframe)
            self.elapsed["native_dp_read_seconds"] += time.perf_counter() - before
            begun = time.perf_counter()
            try:
                if not isinstance(frame, pd.DataFrame):
                    raise TypeError("Native getter must return a real sliced DataFrame")
                digest = semantic_digest(frame)
                expected_refresh = self.refresh.get((pair, timeframe))
                if len(frame) and expected_refresh is not None:
                    if refreshed != expected_refresh:
                        raise ValueError("Native analyzed lastrefresh value changed")
                    if variant == "analyzed_blocks4096_lru32" and refreshed is not expected_refresh:
                        raise ValueError("Block mapping changed original refresh object")
                record = {
                    "phase": self.phase,
                    "at": typed(self.clock),
                    "pair": pair,
                    "timeframe": timeframe,
                    "shape": list(frame.shape),
                    "sha256": digest,
                    "first_date": typed(frame.date.iloc[0]) if len(frame) else None,
                    "last_date": typed(frame.date.iloc[-1]) if len(frame) else None,
                    "callback": self.stack[-1]["name"] if self.stack else None,
                }
                self.append("dp_read_records", record)
                if self.stack:
                    self.stack[-1]["frames"].append((frame, digest))
            except BaseException as error:
                self.error("get_analyzed_dataframe", error)
                raise
            finally:
                self.elapsed["trace_validation_seconds"] += time.perf_counter() - begun
            return frame, refreshed

        def clear():
            before_keys = list(provider._DataProvider__cached_pairs)
            original_clear()
            after_keys = list(provider._DataProvider__cached_pairs)
            self.refresh.clear()
            if after_keys:
                raise ValueError("Native clear did not empty analyzed keys")
            self.append(
                "cache_clear_records",
                {
                    "phase": self.phase,
                    "at": typed(self.clock),
                    "before_keys": typed(before_keys),
                    "after_keys": [],
                },
            )

        provider._set_cached_df = assign
        provider.get_analyzed_dataframe = get
        provider.clear_cache = clear

    def install_strategy(self, strategy):
        original_signals = strategy.ft_advise_signals

        def signals(frame, metadata):
            before = time.perf_counter()
            result = original_signals(frame, metadata)
            self.elapsed["native_signal_seconds"] += time.perf_counter() - before
            begun = time.perf_counter()
            self.append(
                "native_signal_records",
                {
                    "pair": metadata["pair"],
                    "shape": list(result.shape),
                    "sha256": semantic_digest(result),
                },
            )
            self.elapsed["trace_validation_seconds"] += time.perf_counter() - begun
            return result

        strategy.ft_advise_signals = signals
        for name in CALLBACKS:
            operation = getattr(strategy, name)
            signature = inspect.signature(operation)

            def callback(*args, _name=name, _operation=operation, _signature=signature, **kwargs):
                bound = _signature.bind_partial(*args, **kwargs).arguments
                if "current_time" in bound:
                    self.clock = bound["current_time"]
                context = {"name": _name, "frames": []}
                self.stack.append(context)
                begun = time.perf_counter()
                try:
                    inputs = typed(dict(bound))
                    self.state(_name + ":before")
                    if _name == "order_filled":
                        trade, order = bound["trade"], bound["order"]
                        self.append(
                            "order_lifecycle_records",
                            {
                                "kind": filled_kind(trade, order),
                                "filled": True,
                                "at": typed(bound["current_time"]),
                                "pair": bound["pair"],
                                "trade_before_callback": typed(trade),
                                "order": typed(order),
                            },
                        )
                    self.elapsed["trace_validation_seconds"] += time.perf_counter() - begun
                    result = _operation(*args, **kwargs)
                    begun = time.perf_counter()
                    # A callback mutating a borrowed view invalidates the immutable-cache scope.
                    for frame, digest in context["frames"]:
                        if semantic_digest(frame) != digest:
                            raise ValueError("Source callback mutated an analyzed-frame read")
                    self.append(
                        "callback_records",
                        {
                            "callback": _name,
                            "at": typed(self.clock),
                            "inputs": inputs,
                            "return": typed(result),
                        },
                    )
                    self.state(_name + ":after")
                    self.elapsed["trace_validation_seconds"] += time.perf_counter() - begun
                    return result
                except BaseException as error:
                    self.error(_name, error)
                    raise
                finally:
                    self.stack.pop()

            setattr(strategy, name, callback)

    def install_account(self, account, trade_reader, wallet_reader):
        original_reset = account.reset_backtest
        original_phase = account._time_pair_generator_det
        original_capture = account._capture_wallet

        def reset(*args, **kwargs):
            self.phase = "native_reset"
            result = original_reset(*args, **kwargs)
            self.phase = "native_matching"
            return result

        def phase(*args, **kwargs):
            for event in original_phase(*args, **kwargs):
                self.clock = event[0]
                self.fail_if_errors()
                yield event
                self.fail_if_errors()

        def capture(current_time, currency, price):
            result = original_capture(current_time, currency, price)
            if currency == "USDT" and current_time.minute == 0:
                self.clock = current_time
                self.append(
                    "hourly_wallet_records",
                    {"at": typed(current_time), "wallet": typed(wallet_reader())},
                )
            return result

        account.reset_backtest = reset
        account._time_pair_generator_det = phase
        account._capture_wallet = capture

        # Observe pending creation/replacement/cancel/full-close transitions as
        # well as the actual order_filled callbacks. Never call an owner twice.
        for name in ("_enter_trade", "_exit_trade", "manage_open_orders", "_process_exit_order"):
            operation = getattr(account, name)

            def traced(*args, _name=name, _operation=operation, **kwargs):
                tracked = {trade.id: trade for trade in trade_reader()}

                def snapshot():
                    return {
                        (trade.id, str(order.order_id)): typed(order)
                        for trade in tracked.values()
                        for order in trade.orders
                    }

                before = snapshot()
                result = _operation(*args, **kwargs)
                tracked.update({trade.id: trade for trade in trade_reader()})
                if hasattr(result, "orders"):
                    tracked[result.id] = result
                after = snapshot()
                for key in sorted(set(before) | set(after)):
                    if before.get(key) != after.get(key):
                        self.append(
                            "order_lifecycle_records",
                            {
                                "kind": "owner_order_transition",
                                "filled": False,
                                "owner": _name,
                                "trade_id": key[0],
                                "order_id": key[1],
                                "at": typed(self.clock),
                                "before": before.get(key),
                                "after": after.get(key),
                            },
                        )
                return result

            setattr(account, name, traced)

    def close(self):
        if not self.stream.closed:
            self.stream.flush()
            self.stream.close()
