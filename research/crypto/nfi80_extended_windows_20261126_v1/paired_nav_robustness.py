"""Measure the daily concentration and block sensitivity of matched native wallets."""

from math import expm1, isclose
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .inputs import HERE


FEES = (15, 8)
BLOCKS = (14, 28)
DRAWS = 20_000
SEED = 860312
EPSILON = 1e-12


def read_nav(refs):
    nav_ref, = [item for item in refs if Path(item["path"]).suffix == ".parquet"]
    nav = pd.read_parquet(checked(nav_ref), columns=["date", "marked_equity"]).set_index("date").marked_equity
    nav.index = nav.index.as_unit("ns")
    assert nav.index.is_unique and nav.index.is_monotonic_increasing
    assert nav.gt(0).all() and np.isfinite(nav).all()
    return nav, nav_ref


def paired(left, right):
    assert left.index.equals(right.index) and left.iloc[0] == right.iloc[0] == 1000
    midnights = left.index[left.index == left.index.normalize()]
    assert midnights.to_series().diff().dropna().eq(pd.Timedelta(days=1)).all()
    relative = right.loc[midnights].div(left.loc[midnights])
    delta = np.log(relative).diff().iloc[1:].set_axis(midnights[:-1])
    assert isclose(expm1(delta.sum()), relative.iloc[-1] - 1, abs_tol=EPSILON)
    return delta


def path_summary(values):
    total = values.sum()
    selection = values.nlargest(5)
    absolute = values.abs().sum()
    rolling = values.rolling(30).sum().dropna()
    return {
        "first_day_utc": values.index[0].date().isoformat(), "last_day_utc": values.index[-1].date().isoformat(),
        "days": len(values), "relative_cumulative_growth": expm1(total),
        "14_ahead_days": int(values.gt(EPSILON).sum()), "12_ahead_days": int(values.lt(-EPSILON).sum()),
        "tied_days": int(values.abs().le(EPSILON).sum()),
        "best_five_days": {t.date().isoformat(): expm1(v) for t, v in selection.items()},
        "best_five_share_of_absolute_log_differences": float(selection.abs().sum() / absolute) if absolute else None,
        "relative_growth_without_best_day": expm1(total - selection.iloc[0]),
        "relative_growth_without_best_five": expm1(total - selection.sum()),
        "30_day_window_count": len(rolling), "30_day_positive_windows": int(rolling.gt(EPSILON).sum()),
        "calendar_month_relative_growth": {i.strftime("%Y-%m"): expm1(value)
                                           for i, value in values.resample("MS").sum().items()},
    }


def stationary_sample(values, mean_block, draws, seed):
    days, cases = values.shape
    rng = np.random.default_rng(seed)
    results = np.empty((draws, cases))
    for offset in range(0, draws, 256):
        batch = min(256, draws - offset)
        positions = rng.integers(days, size=batch)
        accumulated = values[positions].copy()
        for _ in range(1, days):
            reset = rng.random(batch) < (1 / mean_block)
            positions = np.where(reset, rng.integers(days, size=batch), (positions + 1) % days)
            accumulated += values[positions]
        results[offset:offset + batch] = accumulated
    return results


def block_test(panels):
    values = panels.to_numpy()
    same_case = np.column_stack((values[:, 0], values[:, 0]))
    constant = np.full((9, 2), .01)
    np.testing.assert_allclose(stationary_sample(constant, 14, 17, SEED), .09, rtol=0, atol=EPSILON)
    control = stationary_sample(same_case, 14, 17, SEED)
    np.testing.assert_array_equal(control[:, 0], control[:, 1])
    out = {}
    for period in BLOCKS:
        draws = stationary_sample(values, period, DRAWS, SEED)
        lower, median, upper = np.expm1(np.quantile(draws, [.025, .50, .975], axis=0))
        out[str(period)] = {
            "by_fee": {name: {"lower_95": float(lower[i]), "median": float(median[i]), "upper_95": float(upper[i]),
                             "positive_resample_fraction": float(np.mean(draws[:, i] > EPSILON))}
                       for i, name in enumerate(panels.columns)},
            "all_fees_positive_resample_fraction": float(np.mean((draws > EPSILON).all(axis=1))),
        }
    return out


def main():
    primary = {str(fee): receipt(HERE / f"recent_two_year_{fee}bp_comparison.json") for fee in FEES}
    annual = receipt(HERE / "annual_2024_boost14_15bp_verified.json")
    freeze = write_once(HERE / "paired_nav_robustness_freeze.json", {
        "source": receipt(Path(__file__)), "protocol": receipt(HERE / "paired_nav_robustness_protocol.json"),
        "primary": primary, "annual_2024": annual, "orders_enabled": False,
    })
    paired_series, proof = {}, {}
    for cost, ref in primary.items():
        accounts = read(checked(ref))["accounts"]
        left, right = (read_nav(read(checked(accounts[n]["proof"]))["sources"]) for n in ("12", "14"))
        paired_series[cost] = paired(left[0], right[0])
        proof[cost] = {"12": left[1], "14": right[1]}
    recent = pd.DataFrame(paired_series)
    assert len(recent) == 693 and not recent.isna().any().any()
    previous = read(checked(annual))["accounts"]
    left, right = (read_nav(previous[key]["receipts"]) for key in ("fixed_boost_12", "boost_14"))
    old = paired(left[0], right[0]).to_frame("15")
    assert len(old) == 365
    result = {
        "freeze": freeze, "continuous_native_nav": proof, "annual_native_nav": {"12": left[1], "14": right[1]},
        "continuous": {cost: path_summary(recent[cost]) for cost in recent},
        "continuous_by_year": {str(year): {cost: path_summary(x) for cost, x in data.items()}
                               for year, data in recent.groupby(recent.index.year)},
        "independent_2024": path_summary(old["15"]),
        "paired_stationary_bootstrap": block_test(recent), "standalone_2024_bootstrap": block_test(old),
        "orders_enabled": False,
    }
    write_once(HERE / "paired_nav_robustness_result.json", result)
    print(result["paired_stationary_bootstrap"], flush=True)


if __name__ == "__main__":
    main()
