"""Causal 5m estimates of one-hour common directional market pressure."""

import numpy as np
import pandas as pd


ALT_MINIMUM = 32
HISTORY_HOURS = 168
MIN_SIGMA = 1e-12
Z_LIMIT = 1.5


def scores(closes, btc):
    returns = np.log(closes).diff(12)
    alt = returns.drop(columns=btc)
    valid = alt.count(axis=1)
    values = pd.DataFrame(
        {"btc": returns[btc], "market": alt.median(axis=1).where(valid >= ALT_MINIMUM)}
    )
    result = pd.DataFrame(index=closes.index)
    # Each phase samples non-overlapping hourly returns. The just-completed hour
    # is used in the numerator and excluded from the seven-day RMS denominator.
    for column in values:
        previous = (
            values[column]
            .pow(2)
            .groupby(values.index.minute)
            .transform(
                lambda v: v.shift(1).rolling(HISTORY_HOURS, min_periods=HISTORY_HOURS).mean()
            )
        )
        sigma = np.sqrt(previous)
        result[f"{column}_z"] = values[column] / sigma.where(sigma > MIN_SIGMA)
    result["alts"] = valid
    result["down"] = alt.lt(0).sum(axis=1)
    result["up"] = alt.gt(0).sum(axis=1)
    return result


def flags(row, short):
    adverse = -1 if short else 1
    count = row.up if short else row.down
    btc = np.isfinite(row.btc_z) and adverse * row.btc_z <= -Z_LIMIT
    market = (
        np.isfinite(row.market_z)
        and adverse * row.market_z <= -Z_LIMIT
        and 3 * count >= 2 * row.alts
    )
    return bool(market), bool(btc)
