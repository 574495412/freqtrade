"""Independent implementation of the sourced daily histogram and flat-only rules."""

import math
import numpy as np
import pandas as pd


def daily_profile(highs, lows, volumes, bins=100, coverage=0.70):
    highs, lows, volumes = (np.asarray(v, dtype=float) for v in [highs, lows, volumes])
    if not (len(highs) == len(lows) == len(volumes)) or not len(highs):
        raise ValueError("Aligned nonempty profile arrays required")
    if (
        not all(np.isfinite(v).all() for v in [highs, lows, volumes])
        or (highs < lows).any()
        or (lows <= 0).any()
        or (volumes < 0).any()
    ):
        raise ValueError("Invalid profile observations")
    bottom, top = float(min(lows)), float(max(highs))
    if not top > bottom or not volumes.sum() > 0:
        raise ValueError("Unknown constant/zero-volume daily profile")
    edges = np.linspace(bottom, top, bins + 1)
    weights = np.zeros(bins)
    for high, low, volume in zip(highs, lows, volumes, strict=True):
        if high == low:
            cell = max(0, min(bins - 1, int(np.searchsorted(edges, low, side="left")) - 1))
            weights[cell] += volume
        else:
            first = max(0, min(bins - 1, int(np.searchsorted(edges, low, side="right")) - 1))
            last = max(0, min(bins - 1, int(np.searchsorted(edges, high, side="right")) - 1))
            weights[first : last + 1] += volume / (last - first + 1)
    peak = int(weights.argmax())
    left = right = peak
    mass = float(weights[peak])
    threshold = float(weights.sum() * coverage)
    while mass < threshold and (left or right < bins - 1):
        a = weights[left - 1] if left else -1.0
        b = weights[right + 1] if right < bins - 1 else -1.0
        if b >= a:
            right += 1
            mass += float(weights[right])
        else:
            left -= 1
            mass += float(weights[left])
    return {
        "poc": float((edges[peak] + edges[peak + 1]) / 2),
        "val": float(edges[left]),
        "vah": float(edges[right + 1]),
        "edges": edges,
        "weights": weights,
    }


def features(candles):
    df = candles.copy().sort_values("date").reset_index(drop=True)
    dates = pd.DatetimeIndex(df.date)
    if dates.tz is None or not dates.as_unit("ns").equals(
        pd.date_range(dates[0], dates[-1], freq="15min").as_unit("ns")
    ):
        raise ValueError("Complete observed15mgridrequired")
    if not np.isfinite(df[["open", "high", "low", "close", "volume"]]).all().all():
        raise ValueError("Unknown OHLCV")
    df["day"] = df.date.dt.floor("D")
    profiles = {}
    for day, part in df.groupby("day", sort=True):
        if len(part) != 96:
            if day != df.day.iloc[-1]:
                raise ValueError("Incomplete closed UTC day")
            continue
        profiles[day + pd.Timedelta(days=1)] = daily_profile(part.high, part.low, part.volume)
    for field in ["poc", "vah", "val"]:
        df[field] = [profiles.get(d, {}).get(field, np.nan) for d in df.day]
    df["volume_mean"] = df.volume.rolling(20, min_periods=20).mean().shift(1)
    df["clock"] = df.date + pd.Timedelta(minutes=15)
    df["target_weight"] = 0.0
    df["source_signal"] = 0.0
    df["source_volatility"] = np.nan
    return df.drop(columns="day")


class FlatState:
    def __init__(self, kind):
        if kind not in ["breakout", "fade", "no_volume"]:
            raise ValueError("Unknown policy")
        self.kind = kind
        self.latched = False
        self.outside = 0
        self.extreme = None
        self.count = 0
        self.area = None

    def step(self, row):
        c, h, l, p, upper, lower, v, mean = (
            float(row[k])
            for k in ["close", "high", "low", "poc", "vah", "val", "volume", "volume_mean"]
        )
        if not all(math.isfinite(x) for x in [c, h, l, p, upper, lower, v, mean]):
            raise ValueError("Unknown causal profile inputs")
        if self.kind != "fade":
            side = 1 if c > upper else -1 if c < lower else 0
            if not side:
                self.latched = False
                return None
            if self.latched or v < mean * (0 if self.kind == "no_volume" else 1.5):
                return None
            self.latched = True
            stop = lower if side == 1 else upper
            return {"side": side, "stop": stop, "target": c + side * abs(c - stop)}
        if self.outside == 0:
            if c > upper:
                self.outside = 1
                self.extreme = h
            elif c < lower:
                self.outside = -1
                self.extreme = l
            else:
                return None
            self.area = (p, upper, lower)
            self.count = 0
            return None
        self.count += 1
        side = self.outside
        self.extreme = max(self.extreme, h) if side == 1 else min(self.extreme, l)
        if self.count > 32:
            self.outside = 0
            self.extreme = None
            self.count = 0
            return None
        if (side == 1 and c < self.area[1]) or (side == -1 and c > self.area[2]):
            result = {
                "side": -side,
                "stop": self.extreme,
                "target": self.area[2] if side == 1 else self.area[1],
            }
            self.outside = 0
            self.extreme = None
            self.count = 0
            return result
        return None
