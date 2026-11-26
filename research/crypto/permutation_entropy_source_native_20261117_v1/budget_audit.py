"""Same independent target-quantity audit as sealed TrendCore; preflight path only differs."""
import gzip
import json
import math
from decimal import Decimal as D, ROUND_DOWN
from pathlib import Path
import pandas as pd
HERE = Path(__file__).resolve().parent
def read(path):
    return json.loads(Path(path).read_text())

def budget_audit(candidate, plan, nav, audit):
    directory = Path(plan["output_root"]) / (candidate["name"] + f"_{candidate['cost_bps']}bps")
    with gzip.open(directory / "strategy_trace.json.gz", "rt") as f:
        trace = json.load(f)
    weights = pd.DataFrame(trace["weights"]).set_index("day")
    weights.index = pd.to_datetime(weights.index, utc=True)
    ref = pd.read_parquet(
        Path(plan["output_root"])
        / "preflight_entropytrend_1000_conservative_admission1/native_weights.parquet"
    )
    pd.testing.assert_frame_equal(weights, ref, check_freq=False, check_names=False)
    stats, _ = audit.native_stats(directory, candidate["strategy"])
    orders = {p: [] for p in candidate["pairs"]}
    for trade in stats["trades"]:
        for o in trade["orders"]:
            orders[trade["pair"]].append(o)
            if trade["exit_reason"] == "force_exit" and o is trade["orders"][-1]:
                continue
            stamp = pd.to_datetime(o["order_filled_timestamp"], unit="ms", utc=True)
            assert stamp.hour % 6 == 0 and stamp.minute == stamp.second == 0
    market = read(candidate["market_snapshot"]["path"])["markets"]
    maximum = 0.0
    checked = 0
    config = read(directory / "config.json")
    reserve = 1 + config.get("amount_reserve_percent", 0.05)
    for row in trace["budgets"]:
        stamp = pd.Timestamp(row["clock"])
        assert stamp.hour % 6 == 0 and stamp.minute == 0
        maximum = max(maximum, abs(row["native_marked_equity"] - nav.loc[stamp]))
        assert maximum < 1e-6
        for pair, target in row["target_quantities"].items():
            price = row["prices"][pair]
            equity = row["native_marked_equity"]
            weight = float(weights.at[stamp, pair])
            held = math.fsum(
                o["amount"] * (1 if o["ft_is_entry"] else -1)
                for o in orders[pair]
                if o["order_filled_timestamp"] < stamp.timestamp() * 1000
            )
            if weight != 0 and abs(0.99 * weight - held * price / equity) <= 0.05:
                expected = held
            else:
                step = D(str(market[pair]["precision"]["amount"]))
                expected = float(
                    (
                        D(".99") * D(str(equity)) * D(str(weight)) / D(str(price)) / step
                    ).to_integral_value(rounding=ROUND_DOWN)
                    * step
                )
            limits = market[pair]["limits"]
            minimum = max(
                (limits["cost"]["min"] or 0) * 1.5, (limits["amount"]["min"] or 0) * price * reserve
            )
            if expected * price < minimum:
                expected = 0.0
            assert abs(target - expected) < 1e-10, (
                candidate["name"],
                stamp,
                pair,
                target,
                expected,
            )
            checked += 1
    expected_grid = pd.date_range("2025-01-01T06:00Z", "2026-08-31T18:00Z", freq="6h")
    actual = pd.DatetimeIndex(pd.to_datetime([r["clock"] for r in trace["budgets"]], utc=True))
    assert actual.as_unit("ns").equals(expected_grid.as_unit("ns"))
    return dict(
        account=candidate["name"],
        passed=True,
        budgets=len(actual),
        independently_checked_quantities=checked,
        maximum_budget_nav_error_usdt=maximum,
        nonterminal_fills_only_after_complete_sixhour_signal=True,
        source_weights_identical_across_four_wallets=True,
    )
