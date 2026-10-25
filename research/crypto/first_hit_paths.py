"""Fixed full-position paths: five-minute open profit checks and intrabar hard stops."""

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.exchange import price_to_precision
from freqtrade.exchange.exchange import ROUND_DOWN, ROUND_UP, TICK_SIZE
from freqtrade.research.data import read, sha
from research.crypto.interaction_features import read_market


SPECS = {
    "small": {"floor": 0.008, "atr_multiple": 2, "ceiling": 0.025, "hours": 4, "target_r": 0.5},
    "core": {"floor": 0.015, "atr_multiple": 6, "ceiling": 0.12, "hours": 24, "target_r": 2.0},
}


def causal_ticks(prices):
    """Infer a precision proxy using only already closed candles, never the full month."""
    counts = []
    for name in ["open", "high", "low", "close"]:
        text = prices[name].apply(
            lambda value: np.format_float_positional(
                value, precision=14, unique=False, fractional=False, trim="-"
            )
        )
        counts.append(text.str.extract(r"\.(\d*[1-9])")[0].str.len().fillna(0))
    digits = pd.concat(counts, axis=1).max(axis=1).cummax().shift(1)
    return pd.Series(10.0**-digits, index=prices.index, name="known_tick")


def paths(units, prices, funding, marks, cost_bps=3):
    """Unit-notional labels; neither capital capacity nor account PnL is simulated here."""
    if not prices.index.to_series().diff().dropna().eq(pd.Timedelta(minutes=5)).all():
        raise ValueError("Path crosses a missing five-minute candle")
    fee = 0.0005 + cost_bps / 10000
    begin = pd.DatetimeIndex(units.feature_open) + pd.Timedelta(minutes=10)
    index = prices.index.get_indexer(begin)
    if (index < 0).any():
        raise ValueError("Entry candle missing")
    side = units.side.to_numpy(dtype=int)
    if not np.isin(side, [-1, 1]).all():
        raise ValueError("Invalid direction")
    spec = units.sleeve.map(SPECS)
    cap = np.array([s["hours"] * 12 for s in spec], dtype=int)
    if (index + cap >= len(prices)).any():
        raise ValueError("Full potential path not yet mature")
    first = prices.open.to_numpy()[index]
    distance = np.maximum(
        [s["floor"] for s in spec],
        np.array([s["atr_multiple"] for s in spec]) * units.atr.to_numpy() / first,
    )
    eligible = np.isfinite(distance) & (distance <= [s["ceiling"] for s in spec])
    tick = prices.known_tick.to_numpy()[index]
    if not np.isfinite(tick).all() or (tick <= 0).any():
        raise ValueError("Known entry precision missing")
    stop = np.array(
        [
            price_to_precision(
                rate * (1 - sign * dist),
                quantum,
                TICK_SIZE,
                rounding_mode=ROUND_UP if sign == 1 else ROUND_DOWN,
            )
            for rate, sign, dist, quantum in zip(first, side, distance, tick, strict=True)
        ]
    )
    planned_r = distance + fee * (2 - side * distance)
    target_roi = np.array([s["target_r"] for s in spec]) * planned_r * 2 / (1 + side * fee)
    event_times = pd.DatetimeIndex(funding.index).as_unit("ms").asi8
    if len(event_times) == 0 or not np.isfinite(funding.funding_rate).all():
        raise ValueError("Funding events missing")
    values = marks.reindex(funding.index, method="pad", tolerance=pd.Timedelta(hours=1)).to_numpy()
    if not np.isfinite(values).all():
        raise ValueError("Funding mark missing")
    prefix = np.r_[0.0, np.cumsum(values * funding.funding_rate.to_numpy())]
    starts = np.searchsorted(event_times, begin.as_unit("ms").asi8, side="left")
    clocks = prices.index.as_unit("ms").asi8
    opens, lows, highs = (prices[name].to_numpy() for name in ["open", "low", "high"])
    last, exit_index, reason = (
        np.full(len(units), np.nan),
        np.full(len(units), -1),
        np.full(len(units), "", dtype=object),
    )
    alive = np.ones(len(units), dtype=bool)
    for offset in range(int(cap.max()) + 1):
        active = np.flatnonzero(alive)
        if not len(active):
            break
        at = index[active] + offset
        rate, sign = opens[at], side[active]
        upper = np.searchsorted(event_times, clocks[at], side="right")
        credit = -sign * (prefix[upper] - prefix[starts[active]]) / first[active]
        net = sign * (rate / first[active] - 1) - fee * (1 + rate / first[active]) + credit
        roi = np.round(net * 2 / (1 + sign * fee), 8)
        timeout = offset >= cap[active]
        gap = ~timeout & (sign * (rate - stop[active]) <= 0)
        profit = ~timeout & ~gap & (roi > target_roi[active])
        stopped = (
            ~timeout
            & ~gap
            & ~profit
            & np.where(sign == 1, lows[at] <= stop[active], highs[at] >= stop[active])
        )
        done = timeout | gap | profit | stopped
        chosen = active[done]
        last[chosen] = np.where(stopped[done], stop[chosen], rate[done])
        exit_index[chosen] = at[done]
        reason[chosen] = np.select(
            [timeout[done], gap[done], profit[done], stopped[done]],
            ["first_hit_time", "first_hit_gap", "first_hit_profit", "stop_loss"],
            default="invalid",
        )
        alive[chosen] = False
    if alive.any():
        raise ValueError("Unterminated finite path")
    last = np.array(
        [
            price_to_precision(rate, quantum, TICK_SIZE)
            for rate, quantum in zip(last, tick, strict=True)
        ]
    )
    finish = pd.DatetimeIndex(prices.index[exit_index])
    end = np.searchsorted(event_times, finish.as_unit("ms").asi8, side="right")
    credit = -side * (prefix[end] - prefix[starts]) / first
    price_return, fee_multiplier = side * (last / first - 1), 1 + last / first
    return pd.DataFrame(
        {
            "row_id": units.row_id.to_numpy(),
            "entry": begin,
            "label_known_at": begin + pd.to_timedelta(cap * 5, unit="min"),
            "exit_time": finish,
            "entry_price": first,
            "exit_price": last,
            "price_return": price_return,
            "funding_credit": credit,
            "fee_multiplier": fee_multiplier,
            "net_label_3bps": price_return + credit - fee * fee_multiplier,
            "risk_eligible": eligible,
            "entry_tick_proxy": tick,
            "locked_stop": stop,
            "planned_r": planned_r,
            "target_roi": target_roi,
            "exit_reason": reason,
            "extra_cost_bps_per_side": cost_bps,
        }
    )


@lru_cache(maxsize=40)
def market_bundle(dataset, receipts_sha, symbol):
    dataset = Path(dataset)
    if sha(dataset / "receipts.json") != receipts_sha:
        raise ValueError("Dataset identity changed")
    receipts = read(dataset / "receipts.json")
    price = read_market(dataset, receipts, symbol, "5m-futures")
    price = price.loc[
        price.date.ge(pd.Timestamp("2023-12-01", tz="UTC"))
        & price.date.lt(pd.Timestamp("2025-01-01", tz="UTC"))
    ].set_index("date")
    price["known_tick"] = causal_ticks(price)
    funding = read_market(dataset, receipts, symbol, "1h-funding_rate").set_index("date")
    mark = read_market(dataset, receipts, symbol, "1h-mark").set_index("date").open
    return price, funding, mark


def label_rows(rows, dataset, receipts, cutoff, cost_bps=3):
    digest = sha(Path(dataset) / "receipts.json")
    if read(Path(dataset) / "receipts.json") != receipts:
        raise ValueError("Dataset receipt mismatch")
    results = []
    for symbol, units in rows.groupby("symbol", sort=True):
        price, funding, marks = market_bundle(str(dataset), digest, symbol)
        result = paths(
            units,
            price.loc[price.index < cutoff],
            funding.loc[funding.index < cutoff],
            marks.loc[marks.index < cutoff],
            cost_bps,
        )
        if not result.label_known_at.lt(cutoff).all():
            raise ValueError("Path admitted future outcomes")
        results.append(result)
    return pd.concat(results, ignore_index=True)
