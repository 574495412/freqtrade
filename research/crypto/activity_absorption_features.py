"""Causal five-minute absorption events and nested real-flow/activity feature groups."""

import io
from zipfile import ZipFile

import numpy as np
import pandas as pd

from research.crypto.interaction_features import DIRECTIONAL, FEATURES, UNSIGNED, funding_features


FLOW = ["actual_flow5", "actual_flow60", "flow_change", "flow_price_response"]
ACTIVITY = ["quote_ratio", "count_ratio", "trade_size_ratio", "activity_pressure"]
GROUPS = {
    "Price": FEATURES,
    "Flow": FEATURES + FLOW,
    "FlowActivity": FEATURES + FLOW + ACTIVITY,
}


def read_activity_archive(path):
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != 1 or not names[0].endswith(".csv"):
            raise ValueError("Exactly one official kline CSV required")
        raw = pd.read_csv(io.BytesIO(archive.read(names[0])), header=None)
    if raw.shape[1] != 12:
        raise ValueError("Expected twelve official kline fields")
    if raw.iloc[0, 0] in ("open_time", "open_time_ms"):
        raw = raw.iloc[1:]
    raw = raw.apply(pd.to_numeric, errors="raise")
    unit = "us" if raw.iloc[0, 0] > 1e14 else "ms"
    result = pd.DataFrame(
        {"date": pd.to_datetime(raw[0], unit=unit, utc=True)}
        | {
            name: raw[i].astype(float)
            for name, i in {
                "open": 1,
                "high": 2,
                "low": 3,
                "close": 4,
                "volume": 5,
                "quote_volume": 7,
                "trade_count": 8,
                "taker_buy_quote": 10,
            }.items()
        }
    ).reset_index(drop=True)
    numeric = result.drop(columns="date")
    if not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("Nonfinite activity source")
    if (
        result[["open", "high", "low", "close"]].le(0).any().any()
        or result[["volume", "quote_volume", "trade_count", "taker_buy_quote"]].lt(0).any().any()
        or not result.trade_count.eq(np.floor(result.trade_count)).all()
        or result.taker_buy_quote.gt(result.quote_volume).any()
        or not result.quote_volume.eq(0).equals(result.trade_count.eq(0))
        or result.high.lt(result[["open", "close", "low"]].max(axis=1)).any()
        or result.low.gt(result[["open", "close", "high"]].min(axis=1)).any()
    ):
        raise ValueError("Invalid observed kline activity")
    return result


def dense_price_features(price):
    """Same25 prior-study inputs before hourly sampling; no future price interpolation."""
    close, volume = price.close, price.volume
    prior, delta = close.shift(1), close.diff()
    logs = np.log(close / prior)
    atr = (
        pd.concat(
            [price.high - price.low, (price.high - prior).abs(), (price.low - prior).abs()], axis=1
        )
        .max(axis=1)
        .rolling(14)
        .mean()
    )
    span = (price.high - price.low).replace(0, np.nan)
    typical = (price.high + price.low + close) / 3
    vwap = (typical * volume).rolling(288).sum() / volume.rolling(288).sum().replace(0, np.nan)
    frame = pd.DataFrame(index=price.index)
    for n in [1, 12, 48, 288]:
        frame[f"r{n}"] = np.log(close / close.shift(n))
    frame["atr"], frame["atr_fraction"] = atr, atr / close
    frame["vwap_gap"] = (close - vwap) / atr.replace(0, np.nan)
    for n in [20, 96]:
        frame[f"z{n}"] = (close - close.rolling(n).mean()) / close.rolling(n).std(ddof=0).replace(
            0, np.nan
        )
    frame["location"] = (2 * close - price.high - price.low) / span
    frame["body"] = (close - price.open) / span
    frame["flow12"] = (np.sign(close - price.open) * volume).rolling(12).sum() / volume.rolling(
        12
    ).sum().replace(0, np.nan)
    frame["pressure14"] = delta.rolling(14).sum() / delta.abs().rolling(14).sum().replace(0, np.nan)
    volatility = logs.rolling(288).std(ddof=0)
    frame["vol_ratio"] = logs.rolling(12).std(ddof=0) / volatility.replace(0, np.nan)
    frame["volatility288"] = volatility
    for n in [12, 288]:
        frame[f"efficiency{n}"] = (close - close.shift(n)).abs() / delta.abs().rolling(
            n
        ).sum().replace(0, np.nan)
    frame["log_volume_ratio"] = np.log(
        volume / volume.shift(1).rolling(288).mean().replace(0, np.nan)
    )
    frame["log_dollar_volume"] = np.log((typical * volume).rolling(288).sum().replace(0, np.nan))
    return frame


def activity_features(price):
    quote, count, taker = price.quote_volume, price.trade_count, price.taker_buy_quote
    valid_quote, valid_count = quote.where(quote > 0), count.where(count > 0)
    result = pd.DataFrame(index=price.index)
    result["flow5"] = 2 * taker / valid_quote - 1
    result["flow60"] = 2 * taker.rolling(12).sum() / quote.rolling(12).sum().replace(0, np.nan) - 1
    result["flow_change_raw"] = result.flow5 - result.flow60.shift(1)
    prior_quote = quote.shift(1).rolling(12).mean().replace(0, np.nan)
    prior_count = count.shift(1).rolling(12).mean().replace(0, np.nan)
    result["quote_ratio_raw"] = valid_quote / prior_quote
    result["quote_ratio"] = np.log(result.quote_ratio_raw)
    result["count_ratio"] = np.log(valid_count / prior_count)
    result["trade_size_ratio"] = np.log((valid_quote / valid_count) / (prior_quote / prior_count))
    result["bar_return"] = np.log(price.close / price.open)
    return result.replace([np.inf, -np.inf], np.nan)


def opportunities(price, reference, median, events, start, end):
    if not price.index.equals(reference.index) or not price.index.equals(median.index):
        raise ValueError("Exact common reference clock required")
    if not price.index.to_series().diff().dropna().eq(pd.Timedelta(minutes=5)).all():
        raise ValueError("Complete five-minute calendar required")
    common = dense_price_features(price).join(activity_features(price))
    common = common.join(reference[["r12", "r48", "r288", "volatility288"]].add_prefix("btc_"))
    common = common.join(median.rename("market_median12"))
    common["feature_open"] = common.index
    common["date"] = common.index + pd.Timedelta(minutes=5)
    common = pd.merge_asof(
        common.reset_index(drop=True).sort_values("date"),
        funding_features(events),
        left_on="date",
        right_on="available_at",
        direction="backward",
        tolerance=pd.Timedelta(hours=12),
    )
    records = []
    for side in [1, -1]:
        trigger = (
            (side * common.flow5).le(-0.25)
            & (side * common.bar_return).ge(0)
            & (side * common.location).ge(0.5)
            & common.quote_ratio_raw.ge(1.5)
        )
        fresh = trigger & ~trigger.shift(1, fill_value=False) & common.flow5.shift(1).notna()
        chosen = common.loc[
            fresh
            & common.date.ge(start)
            & (common.date + pd.Timedelta(days=1, minutes=5)).lt(end)
            & common.atr_fraction.between(0.0005, 0.02)
        ].copy()
        frame = chosen[["date", "feature_open", "atr"]].copy()
        for name in DIRECTIONAL:
            frame[f"signed_{name}"] = side * chosen[name]
        frame["signed_btc12"], frame["signed_btc288"] = (
            side * chosen.btc_r12,
            side * chosen.btc_r288,
        )
        frame["signed_relative12"] = side * (chosen.r12 - chosen.market_median12)
        frame["signed_relative48"] = side * (chosen.r48 - chosen.btc_r48)
        frame["funding_credit8"], frame["funding_change8"] = (
            -side * chosen.rate8,
            -side * chosen.change8,
        )
        for name in UNSIGNED:
            frame[name] = chosen.btc_volatility288 if name == "btc_vol" else chosen[name]
        frame["side"], frame["sleeve"], frame["horizon_hours"] = side, "small", 4
        frame["actual_flow5"], frame["actual_flow60"] = side * chosen.flow5, side * chosen.flow60
        frame["flow_change"] = side * chosen.flow_change_raw
        frame["flow_price_response"] = (
            side * chosen.bar_return / chosen.atr_fraction - side * chosen.flow5
        )
        for name in ACTIVITY[:3]:
            frame[name] = chosen[name]
        frame["activity_pressure"] = -side * chosen.flow5 * chosen.count_ratio
        frame = frame.loc[np.isfinite(frame[GROUPS["FlowActivity"]]).all(axis=1)]
        records.append(frame)
    return pd.concat(records, ignore_index=True)
