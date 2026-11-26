"""Read-only native OHLCV/signal receipt including optional absent short flags."""

import hashlib

import numpy as np
import pandas as pd


def signal_receipt(frame, strategy):
    prices = ["open", "high", "low", "close", "volume"]
    flags = ["enter_long", "enter_short", "exit_long", "exit_short"]
    if not set(prices).issubset(frame):
        raise ValueError("Missing native OHLCV receipt fields")
    selected = frame.loc[
        frame.date.ge(pd.Timestamp("2025-01-01T00:00Z"))
        & frame.date.lt(pd.Timestamp("2026-09-01T00:00Z"))
    ]
    dates = pd.DatetimeIndex(selected.date).as_unit("ns").asi8
    # The official engine fills missing short flags with zero after this callback.
    values = (
        selected.reindex(columns=prices + flags, fill_value=0)
        .fillna(0)
        .to_numpy(dtype="<f8", copy=True)
    )
    return {
        "strategy": strategy,
        "rows": len(selected),
        "fields": prices + flags,
        "sha256": hashlib.sha256(
            dates.tobytes() + np.ascontiguousarray(values).tobytes()
        ).hexdigest(),
    }
