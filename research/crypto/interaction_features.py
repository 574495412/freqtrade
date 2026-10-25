"""Causal price, volume and regime features plus fixed-horizon research labels."""

from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import sha


DIRECTIONAL = [
    "r1",
    "r12",
    "r48",
    "r288",
    "vwap_gap",
    "z20",
    "z96",
    "location",
    "body",
    "flow12",
    "pressure14",
]
UNSIGNED = [
    "atr_fraction",
    "vol_ratio",
    "efficiency12",
    "efficiency288",
    "log_volume_ratio",
    "log_dollar_volume",
    "btc_vol",
]
FEATURES = [f"signed_{k}" for k in DIRECTIONAL] + [
    "signed_btc12",
    "signed_btc288",
    "signed_relative12",
    "signed_relative48",
    "funding_credit8",
    "funding_change8",
    *UNSIGNED,
    "side",
]


def hourly_features(price):
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
    frame["atr"] = atr
    frame["atr_fraction"] = atr / close
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
    return frame.loc[frame.index.minute == 0].copy()


def funding_features(events):
    frame = events.sort_values("date").copy()
    hours = frame.date.diff().dt.total_seconds() / 3600
    hours = hours.where(hours.gt(0) & hours.le(12))
    frame["rate8"] = frame.funding_rate * 8 / hours
    frame["change8"] = frame.rate8.diff()
    frame["available_at"] = frame.date + pd.Timedelta(hours=1)
    return frame[["available_at", "rate8", "change8"]]


def opportunity_rows(hourly, reference, median, events, start, end):
    common = hourly.join(
        reference[["r12", "r48", "r288", "volatility288"]].rename(
            columns={k: f"btc_{k}" for k in ["r12", "r48", "r288", "volatility288"]}
        )
    )
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
    if common.available_at.gt(common.date).any():
        raise ValueError("Future funding event used as a feature")
    common = common.loc[
        common.date.ge(start)
        & (common.date + pd.Timedelta(days=1, minutes=5)).lt(end)
        & common.atr_fraction.between(0.0005, 0.02)
    ].copy()
    records = []
    for side in [1, -1]:
        frame = common[["date", "feature_open", "atr"]].copy()
        for name in DIRECTIONAL:
            frame[f"signed_{name}"] = side * common[name]
        frame["signed_btc12"], frame["signed_btc288"] = (
            side * common.btc_r12,
            side * common.btc_r288,
        )
        frame["signed_relative12"] = side * (common.r12 - common.market_median12)
        frame["signed_relative48"] = side * (common.r48 - common.btc_r48)
        frame["funding_credit8"], frame["funding_change8"] = (
            -side * common.rate8,
            -side * common.change8,
        )
        for name in UNSIGNED:
            frame[name] = common.btc_volatility288 if name == "btc_vol" else common[name]
        frame["side"] = side
        frame = frame.loc[np.isfinite(frame[FEATURES]).all(axis=1)].copy()
        for sleeve, hours in [("small", 1), ("core", 24)]:
            sample = frame if sleeve == "small" else frame.loc[frame.feature_open.dt.hour % 4 == 0]
            sample = sample.copy()
            sample["sleeve"], sample["horizon_hours"] = sleeve, hours
            records.append(sample)
    return pd.concat(records, ignore_index=True)


def read_market(dataset, receipts, symbol, kind):
    stem = f"{symbol.removesuffix('USDT')}_USDT_USDT"
    name = f"candles/futures/{stem}-{kind}.feather"
    path = Path(dataset) / name
    if sha(path) != receipts[name]:
        raise ValueError("Registered source data changed")
    frame = pd.read_feather(path)
    if frame.date.duplicated().any() or not frame.date.is_monotonic_increasing:
        raise ValueError("Unique sorted source clocks required")
    return frame


def label_rows(rows, dataset, receipts, cutoff):
    """Net unit-notional targets, not account PnL; no stops, partials or target exits."""
    results = []
    for symbol, units in rows.groupby("symbol", sort=True):
        price = read_market(dataset, receipts, symbol, "5m-futures")
        price = price.loc[
            price.date.ge(pd.Timestamp("2023-12-01", tz="UTC")) & price.date.lt(cutoff)
        ].set_index("date")
        if not price.index.to_series().diff().dropna().eq(pd.Timedelta(minutes=5)).all():
            raise ValueError("Price horizon crosses a missing candle")
        funding = read_market(dataset, receipts, symbol, "1h-funding_rate")
        if funding.date.max() < cutoff:
            raise ValueError("Funding archive coverage ends before label cutoff")
        funding = funding.loc[funding.date.ge(price.index.min()) & funding.date.lt(cutoff)]
        if funding.empty or not np.isfinite(funding.funding_rate).all():
            raise ValueError("Funding settlement value missing")
        if funding.date.diff().dropna().max() > pd.Timedelta(hours=9):
            raise ValueError("Funding event coverage gap")
        mark = read_market(dataset, receipts, symbol, "1h-mark").set_index("date").open
        marks = mark.reindex(
            pd.DatetimeIndex(funding.date), method="pad", tolerance=pd.Timedelta(hours=1)
        ).to_numpy()
        if not np.isfinite(marks).all():
            raise ValueError("Funding mark unavailable")
        funding_time = pd.DatetimeIndex(funding.date).as_unit("ms").asi8
        prefix = np.r_[0.0, np.cumsum(marks * funding.funding_rate.to_numpy())]
        begin = pd.DatetimeIndex(units.feature_open) + pd.Timedelta(minutes=10)
        finish = begin + pd.to_timedelta(units.horizon_hours.to_numpy(), unit="h")
        if not (finish < cutoff).all():
            raise ValueError("Future outcome crossed its authorized cutoff")
        first, last = price.open.reindex(begin).to_numpy(), price.open.reindex(finish).to_numpy()
        if not np.isfinite(first).all() or not np.isfinite(last).all() or (first <= 0).any():
            raise ValueError("Forecast endpoints missing")
        a = np.searchsorted(funding_time, begin.as_unit("ms").asi8, side="left")
        b = np.searchsorted(funding_time, finish.as_unit("ms").asi8, side="right")
        side = units.side.to_numpy()
        credit = -side * (prefix[b] - prefix[a]) / first
        price_return, fee_multiplier = side * (last / first - 1), 1 + last / first
        results.append(
            pd.DataFrame(
                {
                    "row_id": units.row_id.to_numpy(),
                    "entry": begin,
                    "label_known_at": finish,
                    "entry_price": first,
                    "exit_price": last,
                    "price_return": price_return,
                    "funding_credit": credit,
                    "fee_multiplier": fee_multiplier,
                    "net_label_3bps": price_return + credit - 0.0008 * fee_multiplier,
                }
            )
        )
    return pd.concat(results, ignore_index=True)
