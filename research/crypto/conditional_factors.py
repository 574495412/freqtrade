"""Causal conditional factors and explicit non-account barrier labels."""

import numpy as np
import pandas as pd


def features(frame, metrics, events, btc, funding_calculator, coverage_start, coverage_end):
    clock = pd.DatetimeIndex(frame.date)
    if clock.has_duplicates or not clock.is_monotonic_increasing:
        raise ValueError("Price clock must be unique and increasing")
    if not clock.to_series().diff().dropna().eq(pd.Timedelta(minutes=5)).all():
        raise ValueError("Exact continuous five-minute prices required")
    if not btc.index.equals(clock):
        raise ValueError("BTC and contract clocks must match exactly")
    d = frame.set_index("date")
    log_price = np.log(d.close.where(d.close > 0))
    returns = log_price.diff()
    quote = d.quote_volume.where(d.quote_volume > 0)
    buy = d.taker_buy_quote.where(d.taker_buy_quote.ge(0) & d.taker_buy_quote.le(quote))
    flow = 2 * buy / quote - 1
    x, y = flow.shift(4), returns.shift(4)
    mean_x, mean_y = x.rolling(24).mean(), y.rolling(24).mean()
    beta = x.rolling(24).cov(y) / x.rolling(24).var().where(lambda value: value > 0)
    volatility = y.rolling(24).std().where(lambda value: value > 0)
    expected = 4 * (mean_y - beta * mean_x) + beta * flow.rolling(4).sum()
    previous = d.close.shift(1)
    atr = (
        pd.concat([d.high - d.low, (d.high - previous).abs(), (d.low - previous).abs()], axis=1)
        .max(axis=1)
        .rolling(14)
        .mean()
    )
    result = pd.DataFrame(index=clock)
    for name, size in [("return20", 4), ("return60", 12), ("return24h", 288)]:
        result[name] = log_price - log_price.shift(size)
    width = (d.high - d.low).where(lambda value: value > 0)
    result["atr_fraction"] = atr / d.close
    result["close_location"] = 2 * (d.close - d.low) / width - 1
    result["range_fraction"] = (d.high - d.low) / d.close
    result["volume_ratio"] = quote / quote.shift(1).rolling(12).mean()
    result["distance_mean24"] = d.close / d.close.rolling(24).mean() - 1
    result["btc_return24h"] = np.log(btc) - np.log(btc.shift(288))
    for name, size in [("flow20", 4), ("flow60", 12)]:
        result[name] = 2 * buy.rolling(size).sum() / quote.rolling(size).sum() - 1
    result["impact_residual20"] = (result.return20 - expected) / (volatility * 2)
    result["path_efficiency60"] = result.return60.abs() / returns.abs().rolling(12).sum()
    result["flow_exhaustion20"] = -result.flow20 * result.return20 / volatility
    # Dates label candle opens; these calculations are available after open+5minutes.
    completed = clock + pd.Timedelta(minutes=5)
    funding = funding_calculator(
        completed.as_unit("ms").asi8.tolist(), events, coverage_start, coverage_end
    )
    result["funding_sum24h"] = -funding.funding_carry_24h.to_numpy()
    result["funding_change24h"] = -funding.funding_carry_change_24h.to_numpy()
    result["funding_x_return20"] = result.funding_sum24h * result.return20
    result["funding_x_flow20"] = result.funding_sum24h * result.flow20
    source = metrics.set_index("raw_time")
    if source.index.has_duplicates:
        raise ValueError("Duplicate raw OI clock")
    oi = source.sum_open_interest.where(
        source.sum_open_interest.gt(0) & source.sum_open_interest_value_status.eq("KNOWN_RAW_VALUE")
    )
    # At bar close t+5m, the latest allowed raw OI is t-5m, after the10m delay.
    raw_clock = clock - pd.Timedelta(minutes=5)
    now = oi.reindex(raw_clock).to_numpy()
    prior = oi.reindex(raw_clock - pd.Timedelta(minutes=60)).to_numpy()
    result["oi_contraction60"] = np.maximum(-np.log(now / prior), 0)
    for name in ["flow20", "return20", "impact_residual20"]:
        result["oi_contraction_x_" + name] = result.oi_contraction60 * result[name]
    return result.replace([np.inf, -np.inf], np.nan).rename_axis("date").reset_index()


def barrier_labels(frame, sign, take, stop, maximum_bars, fee, events_ms, funding_marks):
    """1x unit-notional labels; stop-first OHLC proxies are never account PnL."""
    if sign not in {-1, 1}:
        raise ValueError("Direction must be long or short")
    n = len(frame)
    opening, high, low = (frame[name].to_numpy(dtype=float) for name in ["open", "high", "low"])
    clocks = pd.DatetimeIndex(frame.date).as_unit("ms").asi8
    entry_index = np.arange(n) + 2
    safe_entry = np.minimum(entry_index, n - 1)
    entry = opening[safe_entry]
    target_price, stop_price = entry * (1 + sign * take), entry * (1 - sign * stop)
    deadline = entry_index + maximum_bars
    exit_price = opening[np.minimum(deadline, n - 1)].copy()
    end_ms = clocks[np.minimum(deadline, n - 1)].copy()
    known = deadline < n
    unresolved = entry_index < n
    for offset in range(maximum_bars):
        at = entry_index + offset
        valid = unresolved & (at < n)
        index = np.minimum(at, n - 1)
        stopped = low[index] <= stop_price if sign == 1 else high[index] >= stop_price
        profitable = high[index] >= target_price if sign == 1 else low[index] <= target_price
        stop_rows = valid & stopped
        take_rows = valid & ~stopped & profitable
        worst = (
            np.minimum(stop_price, opening[index])
            if sign == 1
            else np.maximum(stop_price, opening[index])
        )
        exit_price[stop_rows] = worst[stop_rows]
        exit_price[take_rows] = target_price[take_rows]
        hit = stop_rows | take_rows
        end_ms[hit] = clocks[index[hit]] + 300_000
        known |= hit
        unresolved[hit] = False
    known &= entry_index < n
    events_ms = np.asarray(events_ms, dtype=np.int64)
    funding_marks = np.asarray(funding_marks, dtype=float)
    if len(events_ms) != len(funding_marks) or (np.diff(events_ms) <= 0).any():
        raise ValueError("Unique increasing funding event clock required")
    if not np.isfinite(funding_marks).all():
        raise ValueError("Missing actual funding mark proxy")
    prefix = np.r_[0.0, np.cumsum(funding_marks)]
    first = np.searchsorted(events_ms, clocks[safe_entry], side="left")
    last = np.searchsorted(events_ms, end_ms, side="left")
    net = sign * (exit_price / entry - 1) - fee * (1 + exit_price / entry)
    net -= sign * (prefix[last] - prefix[first]) / entry
    return pd.DataFrame(
        {
            "net_return_label": np.where(known, net, np.nan),
            "net_win_label": np.where(known, net > 0, np.nan),
            "label_known_at_ms": np.where(known, end_ms, np.nan),
            "entry_price": np.where(known, entry, np.nan),
            "exit_price_proxy": np.where(known, exit_price, np.nan),
        }
    )


def expanded_groups(plan):
    groups = {}
    for name, entries in plan["groups"].items():
        columns = []
        for entry in entries:
            columns.extend(groups.get(entry, [entry]))
        if len(set(columns)) != len(columns):
            raise ValueError("Duplicate factor in nested group")
        groups[name] = columns
    return groups
