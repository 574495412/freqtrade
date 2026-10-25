"""Causal two-perpetual funding differential features; no return labels or account profits."""

import argparse
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha


def available(events):
    data = events.sort_values("date").reset_index(drop=True).copy()
    if data.date.duplicated().any() or not data.funding_rate.notna().all():
        raise ValueError("Invalid funding event grain")
    data["available_at"] = data.date + pd.Timedelta(hours=1)
    data["forecast_rate_8h"] = data.funding_rate.ewm(span=3, adjust=False, min_periods=4).mean()
    data["past_three_intervals_are_8h"] = (
        data.date.diff().eq(pd.Timedelta(hours=8)).rolling(3, min_periods=3).sum().eq(3)
    )
    return data[["available_at", "forecast_rate_8h", "past_three_intervals_are_8h"]]


def features(alt, btc, decisions):
    left = pd.DataFrame({"date": decisions})
    for name, events in [("alt", alt), ("btc", btc)]:
        data = available(events).rename(columns=lambda x, n=name: f"{n}_{x}")
        left = pd.merge_asof(
            left,
            data,
            left_on="date",
            right_on=f"{name}_available_at",
            direction="backward",
            tolerance=pd.Timedelta(hours=8),
        )
    left["known_standard_regime"] = left.alt_past_three_intervals_are_8h.eq(
        True
    ) & left.btc_past_three_intervals_are_8h.eq(True)
    difference = left.btc_forecast_rate_8h - left.alt_forecast_rate_8h
    left["long_alt_short_btc"] = difference.gt(0)
    left["forecast_24h_carry_per_margin"] = 3 * difference.abs()
    for name in ["alt", "btc"]:
        valid = left[f"{name}_available_at"].notna()
        if (left.loc[valid, f"{name}_available_at"] > left.loc[valid, "date"]).any():
            raise ValueError("Future event admitted as a feature")
    return left


def run(plan_path, output):
    if output.exists():
        raise ValueError("Preserve prior research")
    plan = read(plan_path)
    if sha(Path(__file__)) != plan["source_sha256"]:
        raise ValueError("Registered feature implementation changed")
    dataset = Path(plan["dataset"])
    receipts = dataset / "receipts.json"
    if sha(receipts) != plan["data_receipts_sha256"]:
        raise ValueError("Dataset receipt changed")
    receipt = read(receipts)
    end = pd.Timestamp(plan["end"], tz="UTC")
    events, inputs = {}, []
    for symbol in plan["symbols"]:
        relative = f"candles/futures/{symbol[:-4]}_USDT_USDT-1h-funding_rate.feather"
        path = dataset / relative
        digest = sha(path)
        if digest != receipt[relative]:
            raise ValueError("Funding events changed")
        data = pd.read_feather(path)
        events[symbol] = data.loc[data.date < end].copy()
        inputs.append({"relative_path": relative, "sha256": digest})
    output.mkdir(parents=True)
    save(
        output / "freeze.json",
        {
            "plan_sha256": sha(plan_path),
            "inputs": inputs,
            "source_sha256": sha(Path(__file__)),
            "frozen_before_feature_computation": True,
        },
    )
    decisions = pd.date_range(
        pd.Timestamp(plan["start"], tz="UTC") + pd.Timedelta(hours=1),
        end,
        freq="8h",
        inclusive="left",
    )
    rows, counts, checks, errors = [], [], 0, []
    btc = events["BTCUSDT"]
    cuts = [pd.Timestamp(x, tz="UTC") for x in plan["prefix_cuts"]]
    for symbol in plan["symbols"]:
        if symbol == "BTCUSDT":
            continue
        alt = events[symbol]
        frame = features(alt, btc, decisions)
        for cut in cuts:
            early = features(
                alt.loc[alt.date + pd.Timedelta(hours=1) <= cut],
                btc.loc[btc.date + pd.Timedelta(hours=1) <= cut],
                decisions[decisions <= cut],
            )
            pd.testing.assert_frame_equal(
                frame.loc[frame.date <= cut].reset_index(drop=True), early, check_exact=True
            )
            checks += 1
        frame.insert(0, "symbol", symbol)
        rows.append(frame)
        valid = frame.loc[frame.known_standard_regime]
        count = {
            "symbol": symbol,
            "decision_rows": len(frame),
            "standard_8h_regime_rows": len(valid),
        }
        for cost in plan["extra_cost_bps_per_side"]:
            total_cost = 4 * (plan["fee_per_side"] + cost / 10000)
            count[f"forecast_carry_exceeds_cost_{cost}bps"] = int(
                valid.forecast_24h_carry_per_margin.gt(total_cost).sum()
            )
        for row in valid.iloc[:: max(len(valid) // 10, 1)].itertuples(index=False):
            exact = Decimal(3) * abs(
                Decimal(str(row.btc_forecast_rate_8h)) - Decimal(str(row.alt_forecast_rate_8h))
            )
            errors.append(abs(float(exact) - row.forecast_24h_carry_per_margin))
        counts.append(count)
    pd.concat(rows, ignore_index=True).to_parquet(output / "features.parquet", index=False)
    totals = {key: sum(r[key] for r in counts) for key in counts[0] if key != "symbol"}
    save(
        output / "result.json",
        {
            "generated_utc": datetime.now(UTC).isoformat(),
            "plan_sha256": sha(plan_path),
            "features_sha256": sha(output / "features.parquet"),
            "counts": counts,
            "totals": totals,
            "exact_prefix_cases_passed": checks,
            "decimal_carry_formula_checks": len(errors),
            "maximum_decimal_error": max(errors),
            "price_or_future_funding_outcome_labels_computed": False,
            "economic_accounts_computed": False,
            "orders_enabled": False,
            "production_eligible": False,
            "scope": plan["scope"],
        },
    )
    print(
        {
            "totals": totals,
            "prefix_cases": checks,
            "decimal_checks": len(errors),
            "outcome_labels_computed": False,
        }
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.plan, args.output)
