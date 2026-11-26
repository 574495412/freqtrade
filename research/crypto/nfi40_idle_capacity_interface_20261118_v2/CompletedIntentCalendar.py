"""Read-only original NFI signal calendar, not an order/allocation simulator."""

from itertools import pairwise

import pandas as pd


STEP_MS = 300000


def direction(long_entry, short_entry, long_exit, short_exit, can_short=True):
    if long_entry == 1 and not (long_exit == 1 or short_entry == 1):
        return "long"
    if can_short and short_entry == 1 and not (short_exit == 1 or long_entry == 1):
        return "short"
    return None


class CompletedIntentCalendar:
    """Capture source signals after unchanged NFI entry+exit callbacks.

    Store dates, intent flags and exact tags only. A snapshot reads exactly the
    completed5m row T-5m, never rowT prices or any acceptance/wallet/outcome data.
    This standalone preparation component is not wired into the disabled wrapper.
    """

    def __init__(self, ordered_pairs, can_short=True):
        if len(ordered_pairs) != len(set(ordered_pairs)) or not ordered_pairs:
            raise ValueError("Ordered unique pool required")
        self.pairs = tuple(ordered_pairs)
        self.can_short = can_short
        self._rows = {}

    def capture(self, pair, frame):
        if pair not in self.pairs or pair in self._rows:
            raise ValueError("Unexpected pair or repeated immutable source capture")
        dates = pd.DatetimeIndex(frame.date)
        if dates.tz is None or str(dates.tz) != "UTC":
            raise ValueError("UTC source dates required")
        clocks = dates.as_unit("ms").asi8.tolist()
        if not clocks or any(b - a != STEP_MS for a, b in pairwise(clocks)):
            raise ValueError("Source calendar must be nonempty continuous5m")
        if any(t % STEP_MS for t in clocks):
            raise ValueError("Source calendar not on5m grid")
        values = frame[["enter_long", "enter_short", "exit_long", "exit_short", "enter_tag"]]
        self._rows[pair] = dict(
            zip(clocks, tuple(values.itertuples(index=False, name=None)), strict=True)
        )

    def snapshot(self, now):
        stamp = pd.Timestamp(now)
        if stamp.tzinfo is None:
            raise ValueError("Aware current clock required")
        current = int(stamp.timestamp() * 1000)
        if current % STEP_MS:
            raise ValueError("Current native clock not on5m grid")
        wanted = current - STEP_MS
        signals = []
        for pair in self.pairs:
            if pair not in self._rows or wanted not in self._rows[pair]:
                raise ValueError("Missing current completed source row; stale fallback forbidden")
            le, se, lx, sx, tag = self._rows[pair][wanted]
            side = direction(le, se, lx, sx, self.can_short)
            if side:
                if (
                    not isinstance(tag, str)
                    or not tag.split()
                    or not all(t.isdigit() for t in tag.split())
                ):
                    raise ValueError("Unknown source tag in original NFI intent calendar")
                signals.append(
                    {
                        "pair": pair,
                        "side": side,
                        "enter_tag": tag,
                        "source_open_ms": wanted,
                        "available_ms": current,
                    }
                )
        return signals
