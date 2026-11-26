"""Correct the equal-boundary initial-cash case without touching financial state."""

import pandas as pd


def complete_initial_cash(frame, start, first_event, capital):
    start, first_event = pd.Timestamp(start), pd.Timestamp(first_event)
    if frame.date.duplicated().any():
        raise ValueError("Duplicate actual observations must not be silently removed")
    if first_event < start:
        raise ValueError("Observation precedes the registered account boundary")
    # pandas date_range(start==end, inclusive='left') can retain that instant.
    # No initial padding is needed when the first actual observation is start.
    if first_event == start:
        return frame
    before = pd.date_range(start, first_event, freq="h", inclusive="left")
    if (frame.date < first_event).any():
        raise ValueError("Pre-event rows already observed")
    rows = pd.DataFrame(
        {
            "date": before,
            "equity": capital,
            "hourly_mark_equity": capital,
            "open_trades": 0,
            "gross": 0,
            "closed_realized_pnl": 0,
        }
    )
    return pd.concat([rows, frame], ignore_index=True)
