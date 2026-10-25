"""Small-sleeve intrabar net-profit targets; the frozen core checkpoint mechanism is a control."""

from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.exchange import price_to_precision
from freqtrade.exchange.exchange import ROUND_DOWN, ROUND_UP, TICK_SIZE
from freqtrade.research.data import read, sha
from research.crypto.first_hit_paths import SPECS as CHECKPOINT_SPECS
from research.crypto.first_hit_paths import market_bundle
from research.crypto.first_hit_paths import paths as checkpoint_paths


SPECS = deepcopy(CHECKPOINT_SPECS)
SPECS["small"]["target_r"] = 0.25


def profit_quotes(first, side, fee, target_cash, credit, tick):
    """Match the native rounded-price profit threshold and its eight-decimal ROI inverse."""
    raw = first * (side + fee + target_cash - credit) / (side - fee)
    quoted = np.array(
        [
            price_to_precision(
                rate, quantum, TICK_SIZE, rounding_mode=ROUND_UP if sign == 1 else ROUND_DOWN
            )
            for rate, quantum, sign in zip(raw, tick, side, strict=True)
        ]
    )
    ratio = quoted / first
    threshold = np.round(
        (side * (ratio - 1) - fee * (1 + ratio) + credit) * 2 / (1 + side * fee), 8
    )
    inverse = first * (side + fee + threshold * (1 + side * fee) / 2 - credit) / (side - fee)
    return threshold, inverse


def small_paths(units, prices, funding, marks, cost_bps=3):
    if not prices.index.to_series().diff().dropna().eq(pd.Timedelta(minutes=5)).all():
        raise ValueError("Missing five-minute path candle")
    begin = pd.DatetimeIndex(units.feature_open) + pd.Timedelta(minutes=10)
    index = prices.index.get_indexer(begin)
    cap = 48
    if (index < 0).any() or (index + cap >= len(prices)).any():
        raise ValueError("Finite path not yet mature")
    fee, side = 0.0005 + cost_bps / 10000, units.side.to_numpy(dtype=int)
    first = prices.open.to_numpy()[index]
    distance = np.maximum(0.008, 2 * units.atr.to_numpy() / first)
    eligible = np.isfinite(distance) & (distance <= 0.025)
    tick = prices.known_tick.to_numpy()[index]
    if not np.isfinite(tick).all() or (tick <= 0).any() or not np.isin(side, [-1, 1]).all():
        raise ValueError("Known precision or direction missing")
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
    risk = distance + fee * (2 - side * distance)
    target_cash = risk * 0.25
    target_roi = target_cash * 2 / (1 + side * fee)
    event_times = pd.DatetimeIndex(funding.index).as_unit("ms").asi8
    values = marks.reindex(funding.index, method="pad", tolerance=pd.Timedelta(hours=1)).to_numpy()
    if (
        not len(event_times)
        or not np.isfinite(values).all()
        or not np.isfinite(funding.funding_rate).all()
    ):
        raise ValueError("Funding settlement or mark missing")
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
    previous_credit, roi_threshold, roi_price = (
        np.full(len(units), np.nan),
        np.zeros(len(units)),
        np.zeros(len(units)),
    )
    for offset in range(cap + 1):
        active = np.flatnonzero(alive)
        if not len(active):
            break
        at = index[active] + offset
        rate, sign = opens[at], side[active]
        upper = np.searchsorted(event_times, clocks[at], side="right")
        credit = -sign * (prefix[upper] - prefix[starts[active]]) / first[active]
        changed = credit != previous_credit[active]
        if changed.any():
            chosen = active[changed]
            roi_threshold[chosen], roi_price[chosen] = profit_quotes(
                first[chosen], side[chosen], fee, target_cash[chosen], credit[changed], tick[chosen]
            )
            previous_credit[chosen] = credit[changed]
        ratio = rate / first[active]
        current_roi = np.round(
            (sign * (ratio - 1) - fee * (1 + ratio) + credit) * 2 / (1 + sign * fee), 8
        )
        timeout = np.full(len(active), offset == cap)
        gap = ~timeout & (sign * (rate - stop[active]) <= 0)
        open_profit = ~timeout & ~gap & (current_roi > target_roi[active])
        stopped = (
            ~timeout
            & ~gap
            & ~open_profit
            & np.where(sign == 1, lows[at] <= stop[active], highs[at] >= stop[active])
        )
        best = np.where(sign == 1, highs[at], lows[at]) / first[active]
        best_roi = np.round(
            (sign * (best - 1) - fee * (1 + best) + credit) * 2 / (1 + sign * fee), 8
        )
        roi = ~timeout & ~gap & ~open_profit & ~stopped & (best_roi > roi_threshold[active])
        done = timeout | gap | open_profit | stopped | roi
        chosen = active[done]
        quote = np.minimum(np.maximum(roi_price[active], lows[at]), highs[at])
        if offset > 0:
            quote = np.where(sign * (rate - roi_price[active]) > 0, rate, quote)
        execution = np.where(stopped, stop[active], np.where(roi, quote, rate))
        last[chosen], exit_index[chosen] = execution[done], at[done]
        reason[chosen] = np.select(
            [timeout[done], gap[done], open_profit[done], stopped[done], roi[done]],
            ["first_hit_time", "first_hit_gap", "first_hit_profit", "stop_loss", "roi"],
            default="invalid",
        )
        alive[chosen] = False
    if alive.any():
        raise ValueError("Unterminated small path")
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
            "label_known_at": begin + pd.Timedelta(hours=4),
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
            "planned_r": risk,
            "target_roi": target_roi,
            "exit_reason": reason,
            "extra_cost_bps_per_side": cost_bps,
        }
    )


def paths(units, prices, funding, marks, cost_bps=3):
    results = []
    for sleeve, group in units.groupby("sleeve", sort=True):
        function = small_paths if sleeve == "small" else checkpoint_paths
        results.append(function(group, prices, funding, marks, cost_bps))
    return pd.concat(results, ignore_index=True)


def label_rows(rows, dataset, receipts, cutoff, cost_bps=3):
    if read(Path(dataset) / "receipts.json") != receipts:
        raise ValueError("Dataset receipt mismatch")
    digest = sha(Path(dataset) / "receipts.json")
    results = []
    for symbol, units in rows.groupby("symbol", sort=True):
        prices, funding, marks = market_bundle(str(dataset), digest, symbol)
        result = paths(
            units,
            prices.loc[prices.index < cutoff],
            funding.loc[funding.index < cutoff],
            marks.loc[marks.index < cutoff],
            cost_bps,
        )
        if not result.label_known_at.lt(cutoff).all():
            raise ValueError("Future path used")
        results.append(result)
    return pd.concat(results, ignore_index=True)
