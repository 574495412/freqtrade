"""Common opportunities and explicit non-account, fixed-horizon net return labels."""

import numpy as np
import pandas as pd


FEATURES = [
    "signed_z",
    "sigma",
    "beta",
    "phi",
    "atr_fraction",
    "signed_residual60",
    "signed_btc60",
    "log_volume_ratio",
    "trend_alignment",
]


def opportunities(panel):
    frame = panel.copy()
    frame["opportunity_side"], frame["opportunity_sleeve"] = 0, ""
    for sign, name in [(1, "long"), (-1, "short")]:
        mask = frame[f"small_{name}"].eq(True)
        frame.loc[mask, ["opportunity_side", "opportunity_sleeve"]] = [sign, "small"]
    # Fixed broad control: core momentum with market trend, no AR1 speed exclusion.
    for sign, edge, regime in [(1, "core_short", "regime_long"), (-1, "core_long", "regime_short")]:
        mask = frame[edge].eq(True) & frame[regime].eq(True)
        frame.loc[mask, ["opportunity_side", "opportunity_sleeve"]] = [sign, "core"]
    return frame.loc[frame.opportunity_side.ne(0)].copy()


def feature_rows(panel, price, reference):
    if not price.index.equals(reference.index):
        raise ValueError("Coin and BTC source clocks must be identical")
    rows = opportunities(panel)
    source = price.reindex(pd.DatetimeIndex(rows.feature_open))
    r60 = np.log(price.close / price.close.shift(12))
    btc60 = np.log(reference.close / reference.close.shift(12))
    volume_ratio = price.volume / price.volume.shift(1).rolling(12).mean().replace(0, np.nan)
    own = r60.reindex(rows.feature_open).to_numpy()
    btc = btc60.reindex(rows.feature_open).to_numpy()
    side = rows.opportunity_side.to_numpy()
    rows["signed_z"] = side * rows.residual_z
    rows["sigma"], rows["phi"] = rows.residual_sigma, rows.ar1_phi
    rows["atr_fraction"] = rows.feature_atr.to_numpy() / source.close.to_numpy()
    rows["signed_residual60"] = side * (own - rows.beta.to_numpy() * btc)
    rows["signed_btc60"] = side * btc
    rows["log_volume_ratio"] = np.log(volume_ratio.reindex(rows.feature_open).to_numpy())
    rows["trend_alignment"] = side * (rows.regime_long.astype(int) - rows.regime_short.astype(int))
    rows["finite_features"] = np.isfinite(rows[FEATURES]).all(axis=1)
    return rows


def maturity_mask(rows, fit_start, fit_end):
    start, end = pd.Timestamp(fit_start), pd.Timestamp(fit_end)
    # Same24h worst-case embargo for both sleeves; never admit by early outcomes.
    fixed_maturity = rows.date + pd.Timedelta(minutes=1445)
    return (rows.date >= start) & (fixed_maturity < end) & rows.finite_features


def horizon_labels(price, rows, events_ms, funding_marks, bars, fee):
    """One-times unit-notional training targets, never account PnL or native fills."""
    source = price.index.get_indexer(pd.DatetimeIndex(rows.feature_open))
    if (source < 0).any():
        raise ValueError("Training source bars missing")
    entry_at, exit_at = source + 2, source + 2 + bars
    if (exit_at >= len(price)).any():
        raise ValueError("Fixed forecast maturity is outside available prices")
    clock = pd.DatetimeIndex(price.index).as_unit("ms").asi8
    if not np.all(clock[exit_at] - clock[entry_at] == bars * 300_000):
        raise ValueError("Forecast horizon crosses a missing price bar")
    opening = price.open.to_numpy(dtype=float)
    entry, exit_ = opening[entry_at], opening[exit_at]
    if not np.isfinite(entry).all() or not np.isfinite(exit_).all() or (entry <= 0).any():
        raise ValueError("Unknown or nonpositive forecast price")
    events_ms, funding_marks = np.asarray(events_ms), np.asarray(funding_marks)
    if len(events_ms) != len(funding_marks) or (np.diff(events_ms) <= 0).any():
        raise ValueError("Unique raw funding clock required")
    if not np.isfinite(funding_marks).all() or len(events_ms) < 2:
        raise ValueError("Actual funding and mark inputs missing")
    if events_ms[0] > clock[entry_at].min() or events_ms[-1] < clock[exit_at].max():
        raise ValueError("Funding source does not cover the fixed horizon population")
    prefix = np.r_[0.0, np.cumsum(funding_marks)]
    begin = np.searchsorted(events_ms, clock[entry_at], side="left")
    end = np.searchsorted(events_ms, clock[exit_at], side="left")
    side = rows.opportunity_side.to_numpy()
    net = side * (exit_ / entry - 1) - fee * (1 + exit_ / entry)
    net -= side * (prefix[end] - prefix[begin]) / entry
    return pd.DataFrame(
        {
            "unit_net_horizon_label": net,
            "horizon_positive_label": (net > 0).astype(int),
            "entry_at_ms": clock[entry_at],
            "label_known_at_ms": clock[exit_at],
            "label_entry_price": entry,
            "label_exit_price": exit_,
        },
        index=rows.index,
    )
