"""Causal cross-contract rotation and delayed global-account crowding states."""

import numpy as np
import pandas as pd


def context_features(panels, closes, metrics):
    symbols = list(panels)
    if len(symbols) != 6 or set(closes) != set(symbols) or set(metrics) != set(symbols):
        raise ValueError("Exactly the six registered contracts required")
    clock = pd.DatetimeIndex(panels[symbols[0]].date)
    if clock.has_duplicates or not clock.is_monotonic_increasing:
        raise ValueError("Unique increasing factor clock required")
    for symbol in symbols:
        if not clock.equals(pd.DatetimeIndex(panels[symbol].date)) or not clock.equals(
            closes[symbol].index
        ):
            raise ValueError("Cross-asset inputs must share an exact clock")
    rotation = pd.DataFrame(
        {symbol: panels[symbol].return20.to_numpy() for symbol in symbols}, index=clock
    )
    complete = np.isfinite(rotation).all(axis=1)
    market_sum = rotation.sum(axis=1, skipna=False)
    dispersion = rotation.std(axis=1, ddof=0, skipna=False)
    breadth = rotation.gt(0).mean(axis=1).where(complete) - 0.5
    btc_return = np.log(closes["BTCUSDT"]).diff()
    prior_x = btc_return.shift(12)
    average_x = prior_x.rolling(288).mean()
    variance_x = prior_x.rolling(288).var().where(lambda value: value > 0)
    result = {}
    for symbol in symbols:
        panel = panels[symbol].set_index("date").copy()
        returns = np.log(closes[symbol]).diff()
        prior_y = returns.shift(12)
        beta = prior_x.rolling(288).cov(prior_y) / variance_x
        residual = panel.return60 - (
            12 * (prior_y.rolling(288).mean() - beta * average_x)
            + beta * (np.log(closes["BTCUSDT"]) - np.log(closes["BTCUSDT"].shift(12)))
        )
        # Regressing BTC onto itself has exactly zero residual, not rounding-noise alpha.
        if symbol == "BTCUSDT":
            residual = residual.where(residual.isna(), 0.0)
        panel["btc_residual60"] = residual
        panel["market_return20"] = ((market_sum - rotation[symbol]) / 5).where(complete)
        panel["relative_return20"] = rotation[symbol] - panel.market_return20
        panel["market_dispersion20"] = dispersion.where(complete)
        panel["breadth20"] = breadth
        denominator = returns.shift(12).rolling(288).std().where(lambda value: value > 0)
        panel["compression60_vs24h"] = returns.rolling(12).std() / denominator
        source = metrics[symbol].set_index("raw_time")
        if source.index.has_duplicates:
            raise ValueError("Duplicate global-account source clock")
        ratio = source.count_long_short_ratio.where(
            source.count_long_short_ratio.gt(0)
            & source.count_long_short_ratio_value_status.eq("KNOWN_RAW_VALUE")
        )
        visible = clock - pd.Timedelta(minutes=5)
        current = np.log(ratio.reindex(visible).to_numpy())
        prior = np.log(ratio.reindex(visible - pd.Timedelta(minutes=60)).to_numpy())
        panel["global_account_logratio"] = current
        panel["global_account_change60"] = current - prior
        panel["crowding_x_residual20"] = current * panel.impact_residual20
        panel["crowding_x_flow20"] = current * panel.flow20
        panel["funding_x_crowding"] = current * panel.funding_sum24h
        panel["contraction_x_crowding"] = current * panel.oi_contraction60
        result[symbol] = panel.replace([np.inf, -np.inf], np.nan).reset_index()
    return result
