"""Check conditional correlation of new positions against the observed native book."""

from functools import lru_cache
from math import fsum, isfinite
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .annual_trade_scale import FIVE_MINUTES
from .continuous_native_inputs import IndependentShardRead, candle_filename
from .inputs import HERE
from .marginal_gross_guard import COMPARISONS, STRESS
from .recent_two_year_attribution import ledger


DAY = pd.Timedelta(days=1)
MARKET = "BTC/USDT:USDT"
WINDOW = 60


def close_at_availability(raw):
    dates = pd.DatetimeIndex(raw.date).as_unit("ns") + DAY
    assert dates.is_unique and dates.is_monotonic_increasing and dates.equals(dates.normalize())
    valid = raw.close.gt(0) & raw.volume.gt(0) & np.isfinite(raw.close) & np.isfinite(raw.volume)
    return pd.Series(raw.close.where(valid).to_numpy(), index=dates)


def open_book(trades, clock):
    tick = clock.value // 1_000_000
    held = {}
    for trade in trades:
        if not trade["open_timestamp"] < tick <= trade["close_timestamp"]:
            continue
        assert trade["pair"] not in held
        qty = fsum(
            o["amount"] if o["ft_is_entry"] else -o["amount"] for o in trade["orders"]
            if o["order_filled_timestamp"] < tick
        )
        assert qty > 0 and trade["leverage"] > 0
        held[trade["pair"]] = qty, trade["leverage"], trade["is_short"]
    return held


def measure(candidate, clock, longs, nav, close):
    last = (clock - FIVE_MINUTES).normalize()
    days = pd.date_range(last - WINDOW * DAY, last, freq="D")
    assert days[-1] < clock
    prices = pd.DataFrame({
        pair: close(pair).reindex(days)
        for pair in dict.fromkeys((MARKET, candidate, *longs))
    })
    returns = np.log(prices).diff().iloc[1:]
    down = returns[MARKET].lt(0)
    unknown = prices.columns[prices.isna().any(axis=0)].tolist()
    note = {"latest_daily_availability_utc": last.isoformat(), "btc_negative_days": int(down.sum()),
            "existing_long_pairs": list(longs), "missing_complete_history": unknown}
    if unknown or down.sum() < 20:
        return note
    correlations = {}
    for pair, holding in longs.items():
        qty, leverage = holding
        margin = prices[pair].iat[-1] * qty / leverage
        pair_return = returns[[candidate, pair]]
        conditional = pair_return.loc[down]
        rho = float(pair_return[candidate].corr(pair_return[pair]))
        bearish = float(conditional[candidate].corr(conditional[pair]))
        if not (isfinite(rho) and isfinite(bearish)):
            return {**note, "zero_variance": pair}
        np.testing.assert_allclose(bearish, np.corrcoef(conditional.to_numpy(), rowvar=False)[0, 1],
                                   atol=1e-12, rtol=0)
        correlations[pair] = {"all": rho, "btc_down": bearish, "margin_over_nav": margin / nav}
    totals = {regime: fsum(max(0, row[regime]) * row["margin_over_nav"] for row in correlations.values())
              for regime in ("all", "btc_down")}
    return {**note, "weighted_overlap": totals, "position_correlations": correlations}


def screen(rows, curve, archive, load):
    trades = stats_from(checked(archive))["trades"]

    @lru_cache(maxsize=None)
    def close(pair):
        return close_at_availability(load(pair))

    result = []
    for row in rows:
        lower, upper = row["entry_slot_bounds"]
        decision, evidence = "outside_rule", {}
        if upper >= 11 and row["side"] == "long":
            clock = pd.Timestamp(row["first_fill"])
            book = open_book(trades, clock)
            assert len(book) == curve.at[clock, "open_trades"]
            assert row["pair"] not in book
            longs = {pair: (qty, leverage) for pair, (qty, leverage, short) in book.items() if not short}
            nav = float(curve.at[clock, "marked_equity"])
            assert nav > 0
            evidence = measure(row["pair"], clock, longs, nav, close)
            totals = evidence.get("weighted_overlap")
            decision = "unknown"
            if totals is not None:
                if totals["btc_down"] <= max(.30, totals["all"]):
                    decision = "would_allow"
                elif lower >= 11:
                    decision = "would_block"
        result.append({**row, **evidence, "decision": decision})
    return result


def earlier_capacity(stress, gross_rows):
    refs = stress["account_sources"]["14"]
    source = read(checked(refs["native_plan"]))
    fingerprints = read(checked(source["data_receipts"]))
    curve = pd.read_parquet(checked(refs["nav"])).set_index("date")

    def load(pair):
        file = candle_filename(pair, "1d-futures")
        return pd.read_feather(checked({"path": str(Path(source["datadir"]) / file), "sha256": fingerprints[file]}))

    return screen(gross_rows, curve, refs["native_trades"], load)


def recent_capacity(comparison, gross_rows):
    reference = comparison["accounts"]["14"]
    wallet = ledger(read(checked(reference["proof"])))
    plan = read(checked(reference["registered_plan"]))
    reader = IndependentShardRead(plan["data_receipts"])
    return screen(gross_rows, wallet["curve"], wallet["refs"][".zip"], lambda pair: reader(pair, "1d-futures"))


def main():
    checks = receipt(HERE / "marginal_gross_guard_result.json")
    stress, protocol = receipt(STRESS), receipt(HERE / "downside_overlap_protocol.json")
    recent = {str(fee): receipt(path) for fee, path in COMPARISONS.items()}
    freeze = write_once(HERE / "downside_overlap_freeze.json", {
        "source": receipt(Path(__file__)), "prior_slots": checks, "stress": stress,
        "recent": recent, "protocol": protocol, "orders_enabled": False,
    })
    cases = read(checked(checks))
    earlier = earlier_capacity(read(checked(stress)), cases["stress_2024"])
    matched = {fee: recent_capacity(read(checked(ref)), cases["near_two_years"][fee]) for fee, ref in recent.items()}
    hits = [row["pair"] for row in earlier if row["entry_slot_bounds"] in ([11, 11], [12, 12])
            and row["decision"] == "would_block"]
    winners = {fee: all(row["decision"] in ("outside_rule", "would_allow") for row in rows
                        if row["over_1pct_of_prior_equity"]) for fee, rows in matched.items()}
    write_once(HERE / "downside_overlap_result.json", {
        "freeze": freeze, "stress_2024": earlier, "near_two_years": matched,
        "definite_11_or_12_stress_blocks": hits, "large_winners_retained": winners,
        "advance": bool(hits) and all(winners.values()), "orders_enabled": False,
    })
    print(hits, winners, flush=True)


if __name__ == "__main__":
    main()
