"""Three preregistered signals; future quotes and outcome labels never rank them."""

import numpy as np
import pandas as pd

from research.crypto.microstructure_features import FACTOR_NAMES


NAMES = ["ReceivedFlowControl", "ReceivedBookContinuation", "ReceivedBookAbsorption"]
COLUMNS = [
    "candidate",
    "symbol",
    "sign",
    "decision_wall_ns",
    "decision_monotonic_ns",
    "feature_monotonic_ns",
    "score",
    "past30second_mid_return_bps",
]


def conditions(row, past_return):
    flow, slow = row.received_flow_30s, row.received_flow_120s
    direction = 1 if flow > 0 else -1
    strong = abs(flow) >= 0.5 and direction * slow >= 0.2
    continuation = strong and direction * row.persistence_30s >= 2 / 3
    result = []
    if continuation:
        result.append((NAMES[0], direction))
        if direction * row.imbalance_l5 >= 0.2 and direction * row.microprice_deviation_bps > 0:
            result.append((NAMES[1], direction))
    reverse = -direction
    if (
        strong
        and reverse * row.imbalance_l5 >= 0.2
        and reverse * row.microprice_deviation_bps > 0
        and np.isfinite(past_return)
        and abs(past_return) <= 5
    ):
        result.append((NAMES[2], reverse))
    return result


def rank_proposals(proposals):
    if proposals.empty:
        return pd.DataFrame(columns=COLUMNS)
    return (
        proposals.sort_values(
            ["candidate", "decision_wall_ns", "score", "symbol"],
            ascending=[True, True, False, True],
            kind="stable",
        )
        .drop_duplicates(["candidate", "decision_wall_ns"])
        .reset_index(drop=True)
    )


def proposals(features, quotes, trade_clocks, plan):
    if set(plan["candidates"]) != set(NAMES):
        raise ValueError("Only the preregistered three-candidate protocol supported")
    collected, population = [], []
    for symbol in plan["symbols"]:
        frame = features.loc[features.symbol.eq(symbol)].sort_values("decision_wall_ns")
        times = frame.decision_wall_ns.to_numpy(np.int64)
        if not len(times) or (np.diff(times) <= 0).any():
            raise ValueError("Unique increasing feature clock required")
        start = ((int(times[0]) + 60_000_000_000 - 1) // 60_000_000_000) * 60_000_000_000
        q = quotes[symbol]
        trade = trade_clocks[symbol]
        trade_times = trade.mono_ns.to_numpy(np.int64)
        if (np.diff(trade_times) < 0).any():
            raise ValueError("Increasing trade receipt order required")
        bad = np.r_[0, np.cumsum(~trade.valid.to_numpy(bool))]
        counts = {
            "symbol": symbol,
            "minute_decisions": 0,
            "common_complete_decisions": 0,
            "absorption_past_price_available": 0,
        }
        for cut in range(start, int(times[-1]) + 1, 60_000_000_000):
            counts["minute_decisions"] += 1
            index = int(np.searchsorted(times, cut, side="right") - 1)
            row = frame.iloc[index]
            if (
                cut - times[index] > 1_000_000_000
                or not row.complete_factors
                or not np.isfinite(row[FACTOR_NAMES].to_numpy(float)).all()
                or row.spread_bps > plan["common_filters"]["maximum_spread_bps"]
            ):
                continue
            now = int(row.decision_monotonic_ns) + cut - int(row.decision_wall_ns)
            feature_book = q.previous(int(row.decision_monotonic_ns))
            current_book = q.previous(now)
            if feature_book is None or current_book is None:
                continue
            if q.frame.age_upper_ms.iloc[feature_book] + (now - q.time[feature_book]) / 1e6 > 1000:
                continue
            lower = np.searchsorted(
                trade_times, int(row.decision_monotonic_ns) - 120_000_000_000, side="right"
            )
            upper = np.searchsorted(trade_times, int(row.decision_monotonic_ns), side="right")
            if lower == upper or bad[upper] != bad[lower]:
                continue
            counts["common_complete_decisions"] += 1
            past = q.previous(now - 30_000_000_000)
            past_return = (
                (10_000 * (q.mid(current_book) / q.mid(past) - 1)) if past is not None else np.nan
            )
            if np.isfinite(past_return):
                counts["absorption_past_price_available"] += 1
            for candidate, sign in conditions(row, past_return):
                collected.append(
                    {
                        "candidate": candidate,
                        "symbol": symbol,
                        "sign": sign,
                        "decision_wall_ns": cut,
                        "decision_monotonic_ns": now,
                        "feature_monotonic_ns": int(row.decision_monotonic_ns),
                        "score": float(abs(row.received_flow_30s) * abs(row.received_flow_120s)),
                        "past30second_mid_return_bps": float(past_return),
                    }
                )
        population.append(counts)
    return rank_proposals(pd.DataFrame(collected, columns=COLUMNS)), population
