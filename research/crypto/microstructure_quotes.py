"""Observed quote paths with latency, bounded clocks and explicit unknowns."""

from decimal import Decimal, localcontext

import numpy as np
import pandas as pd


class QuoteSeries:
    def __init__(self, frame):
        required = [
            "mono_ns",
            "wall_ns",
            "bid",
            "ask",
            "valid",
            "epoch",
            "event_ms",
            "server_lower_ms",
            "server_upper_ms",
            "age_upper_ms",
        ]
        if not set(required).issubset(frame.columns):
            raise ValueError("Missing quote provenance")
        self.frame = frame.reset_index(drop=True)
        self.time = frame.mono_ns.to_numpy(dtype=np.int64)
        if len(self.time) < 2 or (np.diff(self.time) <= 0).any():
            raise ValueError("Unique increasing receive clocks required")
        self.bid, self.ask = frame.bid.to_numpy(float), frame.ask.to_numpy(float)
        self.valid = frame.valid.to_numpy(dtype=bool, copy=True)
        self.valid &= np.isfinite(self.bid) & np.isfinite(self.ask)
        self.valid &= (self.bid > 0) & (self.ask > self.bid)
        provenance = frame[["server_lower_ms", "server_upper_ms", "age_upper_ms"]].to_numpy(float)
        self.valid &= np.isfinite(provenance).all(axis=1)
        self.valid &= provenance[:, 1] >= provenance[:, 0]
        self.valid &= (provenance[:, 2] >= 0) & (provenance[:, 2] <= 1000)
        gap = np.r_[False, np.diff(self.time) > 1_000_000_000]
        epoch = frame.epoch.to_numpy()
        gap[1:] |= epoch[1:] != epoch[:-1]
        if "source_gap" in frame:
            gap |= frame.source_gap.to_numpy(bool)
        self.bad = np.r_[0, np.cumsum(~self.valid | gap)]

    def previous(self, now, maximum_age_ns=1_000_000_000):
        index = int(np.searchsorted(self.time, now, side="right") - 1)
        if index < 0 or now - self.time[index] > maximum_age_ns or not self.valid[index]:
            return None
        if self.frame.age_upper_ms.iloc[index] + (now - self.time[index]) / 1e6 > 1000:
            return None
        return index

    def execute(self, not_before, maximum_delay_ns, entry_index=None):
        index = int(np.searchsorted(self.time, not_before, side="left"))
        while index < len(self.time) and self.time[index] <= not_before + maximum_delay_ns:
            if self.valid[index]:
                if entry_index is not None and self.bad[index + 1] != self.bad[entry_index]:
                    return None
                return index
            if entry_index is not None:
                return None
            index += 1
        return None

    def mid(self, index):
        return (self.bid[index] + self.ask[index]) / 2

    def decimal_price(self, index, side):
        column = f"{side}_text"
        return (
            Decimal(self.frame[column].iloc[index])
            if column in self.frame
            else Decimal(str(self.bid[index] if side == "bid" else self.ask[index]))
        )


def quote_path(quotes, decision_ns, sign, spec, capture_end_ns):
    """Locate observed entry/exit; never fill at the barrier or reuse a stale quote."""
    if sign not in [-1, 1]:
        raise ValueError("Direction must be long or short")
    holding_ns = spec["maximum_holding_seconds"] * 1_000_000_000
    if decision_ns + holding_ns + 4_000_000_000 >= capture_end_ns:
        return {"known": False, "reason": "fixed_horizon_tail"}
    opening = quotes.execute(decision_ns + 1_000_000_000, 1_000_000_000)
    if opening is None:
        return {"known": False, "reason": "missing_entry_quote"}
    entry_price = quotes.ask[opening] if sign == 1 else quotes.bid[opening]
    deadline = quotes.time[opening] + holding_ns
    take = spec["take_price_fraction"]
    stop = spec["stop_price_fraction"]
    with localcontext() as context:
        context.prec = 40
        exact_entry = quotes.decimal_price(opening, "ask" if sign == 1 else "bid")
        take_price = exact_entry * (1 + sign * Decimal(str(take)))
        stop_price = exact_entry * (1 - sign * Decimal(str(stop)))
    reason, trigger_ns, closing = "time", int(deadline), None
    for index in range(opening, len(quotes.time)):
        if quotes.bad[index + 1] != quotes.bad[opening]:
            return {"known": False, "reason": "quote_path_gap_or_invalid"}
        if quotes.time[index] >= deadline:
            closing = quotes.execute(deadline, 1_000_000_000, opening)
            break
        executable = quotes.decimal_price(index, "bid" if sign == 1 else "ask")
        hit_take = executable >= take_price if sign == 1 else executable <= take_price
        hit_stop = executable <= stop_price if sign == 1 else executable >= stop_price
        if hit_take or hit_stop:
            reason = "take" if hit_take else "stop"
            trigger_ns = int(quotes.time[index])
            closing = quotes.execute(trigger_ns + 1_000_000_000, 1_000_000_000, opening)
            break
    if closing is None:
        return {"known": False, "reason": "missing_exit_quote"}
    exit_price = quotes.bid[closing] if sign == 1 else quotes.ask[closing]
    return {
        "known": True,
        "reason": reason,
        "sign": sign,
        "entry_index": opening,
        "exit_index": closing,
        "entry_monotonic_ns": int(quotes.time[opening]),
        "exit_monotonic_ns": int(quotes.time[closing]),
        "trigger_monotonic_ns": trigger_ns,
        "entry_price": float(entry_price),
        "exit_price": float(exit_price),
        "gross_unit_return": float(sign * (exit_price / entry_price - 1)),
        "account_return_measured": False,
    }


def funding_cash(quotes, path, events, coverage):
    """Cash per one base unit; ambiguous exposure boundaries stay unknown."""
    opening, closing = path["entry_index"], path["exit_index"]
    entry = quotes.frame.iloc[opening]
    exit_quote = quotes.frame.iloc[closing]
    if (
        coverage["complete"] is not True
        or coverage["start_ms"] > coverage["end_ms"]
        or coverage["start_ms"] > entry.server_lower_ms
        or coverage["end_ms"] < exit_quote.server_upper_ms
    ):
        return {"known": False, "reason": "unknown_funding_coverage"}
    cash, charged = 0.0, []
    for event in events:
        at, rate = event["time_ms"], event["rate"]
        if not np.isfinite([at, rate]).all():
            return {"known": False, "reason": "invalid_funding_event"}
        if at < entry.server_lower_ms or at >= exit_quote.server_upper_ms or rate == 0:
            continue
        if at < entry.server_upper_ms or at >= exit_quote.server_lower_ms:
            return {"known": False, "reason": "ambiguous_funding_exposure_boundary"}
        safe = np.flatnonzero(
            (quotes.frame.server_upper_ms.to_numpy() <= at)
            & (quotes.frame.event_ms.to_numpy() <= at)
            & (at - quotes.frame.event_ms.to_numpy() <= 1000)
            & quotes.valid
        )
        if not len(safe):
            return {"known": False, "reason": "missing_contemporaneous_funding_mid_proxy"}
        index = int(safe[-1])
        mid = quotes.mid(index)
        amount = -path["sign"] * rate * mid
        cash += amount
        charged.append(
            {
                "time_ms": at,
                "rate": rate,
                "observed_mid_proxy": float(mid),
                "cash_per_base_unit": float(amount),
            }
        )
    return {"known": True, "cash_per_base_unit": cash, "events": charged}


def net_labels(path, funding, costs):
    if not path["known"] or not funding["known"]:
        raise ValueError("Unknown quote or funding path cannot become a net label")
    ratio = path["exit_price"] / path["entry_price"]
    carry = funding["cash_per_base_unit"] / path["entry_price"]
    return {
        str(extra): path["gross_unit_return"]
        + carry
        - (costs["base_fee"] + extra / 10_000) * (1 + ratio)
        for extra in costs["extra_bps"]
    }


def clocks_for_receipts(records, samples, filters):
    """As-of clock bounds; a later probe cannot improve an older observation."""
    received = records.mono_ns.to_numpy(np.int64)
    wall = records.wall_ns.to_numpy(np.int64) / 1e6
    samples = pd.DataFrame(samples)
    if samples.empty:
        return pd.DataFrame(
            {
                "age_upper_ms": np.nan,
                "server_lower_ms": np.nan,
                "server_upper_ms": np.nan,
                "clock_valid": False,
            },
            index=records.index,
        )
    times = samples.received_monotonic_ns.to_numpy(np.int64)
    if (np.diff(times) <= 0).any():
        raise ValueError("Increasing clock sample receipt order required")
    indices = np.searchsorted(times, received, side="right") - 1
    safe = np.maximum(indices, 0)
    rtt = (samples.received_monotonic_ns - samples.sent_monotonic_ns).to_numpy() / 1e6
    lower = (samples.server_time_ms - samples.received_wall_ns / 1e6 - 1).to_numpy()[safe]
    upper = (samples.server_time_ms - samples.sent_wall_ns / 1e6 + 1).to_numpy()[safe]
    age = wall + upper - records.event_ms.to_numpy()
    known = (indices >= 0) & (
        received - times[safe] <= filters["maximum_clock_probe_age_seconds"] * 1e9
    )
    known &= (rtt[safe] >= filters["minimum_clock_probe_rtt_ms"]) & (
        rtt[safe] <= filters["maximum_clock_probe_rtt_ms"]
    )
    known &= (age >= 0) & (age <= filters["maximum_conservative_event_age_ms"])
    return pd.DataFrame(
        {
            "age_upper_ms": np.where(indices >= 0, age, np.nan),
            "server_lower_ms": np.where(indices >= 0, wall + lower, np.nan),
            "server_upper_ms": np.where(indices >= 0, wall + upper, np.nan),
            "clock_valid": known,
        },
        index=records.index,
    )
