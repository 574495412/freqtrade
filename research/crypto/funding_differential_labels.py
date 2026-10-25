"""Replay fixed 24-hour two-perpetual unit labels for preregistered funding opportunities."""

import argparse
import math
from decimal import Decimal
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha


def run(plan_path, output):  # noqa: C901 - explicit two-leg chronology and cash audit
    if output.exists():
        raise ValueError("Preserve previous labels")
    plan = read(plan_path)
    if plan["source_sha256"] != sha(Path(__file__)):
        raise ValueError("Registered label code changed")
    dataset, cache = Path(plan["dataset"]), {}
    receipts = dataset / "receipts.json"
    if sha(receipts) != plan["data_receipts_sha256"]:
        raise ValueError("Dataset receipt changed")
    hashes = read(receipts)
    source = Path(plan["features"])
    if sha(source) != plan["features_sha256"]:
        raise ValueError("Feature data changed")
    features = pd.read_parquet(source)
    selected = features.loc[
        features.known_standard_regime
        & features.forecast_24h_carry_per_margin.gt(plan["cost_per_margin"])
    ]
    if len(selected) != plan["enrolled_units"]:
        raise ValueError("Enrollment changed")
    output.mkdir(parents=True)
    save(
        output / "freeze.json",
        {
            "plan_sha256": sha(plan_path),
            "source_sha256": sha(Path(__file__)),
            "features_sha256": sha(source),
            "frozen_before_outcomes": True,
        },
    )

    def data(symbol):
        if symbol not in cache:
            stem = f"{symbol[:-4]}_USDT_USDT"
            loaded = []
            for suffix in ["5m-futures", "1h-mark", "1h-funding_rate"]:
                relative = f"candles/futures/{stem}-{suffix}.feather"
                path = dataset / relative
                if sha(path) != hashes[relative]:
                    raise ValueError("Frozen market data changed")
                frame = pd.read_feather(path).set_index("date")
                if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
                    raise ValueError("Invalid source clock")
                loaded.append(frame)
            cache[symbol] = loaded
        return cache[symbol]

    rows, errors, ledgers = [], [], []
    for row in selected.itertuples(index=False):
        entry, close = (
            row.date + pd.Timedelta(minutes=5),
            row.date + pd.Timedelta(days=1, minutes=5),
        )
        if close >= pd.Timestamp(plan["end"], tz="UTC"):
            raise ValueError("Unit did not mature inside the study window")
        prices, funding_cash, fees = [], [], []
        exact, legs = Decimal(0), []
        for symbol, side in [
            (row.symbol, 1 if row.long_alt_short_btc else -1),
            ("BTCUSDT", -1 if row.long_alt_short_btc else 1),
        ]:
            candles, mark, funding = data(symbol)
            first, final = float(candles.loc[entry, "open"]), float(candles.loc[close, "open"])
            quantity = 1 / first
            events = funding.loc[(funding.index >= entry) & (funding.index < close)]
            event_rows = []
            for when, event in events.iterrows():
                index = mark.index.get_indexer(
                    [when], method="pad", tolerance=pd.Timedelta(hours=1)
                )[0]
                if index < 0:
                    raise ValueError("No contemporaneous funding mark proxy")
                event_rows.append(
                    {
                        "date": when.isoformat(),
                        "rate": float(event.funding_rate),
                        "mark_date": mark.index[index].isoformat(),
                        "mark": float(mark.iloc[index].open),
                    }
                )
            price_pnl = side * quantity * (final - first)
            credit = -side * quantity * math.fsum(e["rate"] * e["mark"] for e in event_rows)
            fee = quantity * (first + final) * plan["fee_per_side_with_cost"]
            q = Decimal(1) / Decimal(str(first))
            exact += Decimal(side) * q * (Decimal(str(final)) - Decimal(str(first)))
            exact -= (
                Decimal(side)
                * q
                * sum(
                    (Decimal(str(e["rate"])) * Decimal(str(e["mark"])) for e in event_rows),
                    Decimal(0),
                )
            )
            exact -= (
                q
                * (Decimal(str(first)) + Decimal(str(final)))
                * Decimal(str(plan["fee_per_side_with_cost"]))
            )
            prices.append(price_pnl)
            funding_cash.append(credit)
            fees.append(fee)
            legs.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "entry_price": first,
                    "exit_price": final,
                    "funding_events": event_rows,
                }
            )
        net = math.fsum(prices) + math.fsum(funding_cash) - math.fsum(fees)
        errors.append(abs(net - float(exact)))
        rows.append(
            {
                "symbol": row.symbol,
                "decision_time": row.date,
                "entry": entry,
                "exit": close,
                "forecast_carry_per_margin": row.forecast_24h_carry_per_margin,
                "price_profit_per_margin": math.fsum(prices),
                "funding_per_margin": math.fsum(funding_cash),
                "fees_per_margin": math.fsum(fees),
                "net_label_per_margin": net,
            }
        )
        ledgers.append(
            {
                "symbol": row.symbol,
                "entry": entry.isoformat(),
                "exit": close.isoformat(),
                "legs": legs,
            }
        )
    result = pd.DataFrame(rows)
    result.to_parquet(output / "unit_labels.parquet", index=False)
    save(output / "unit_ledgers.json", ledgers)
    groups = {}
    for name, subset in [
        ("all", result),
        ("H1", result.loc[result.decision_time < pd.Timestamp("2024-07-01", tz="UTC")]),
        ("H2", result.loc[result.decision_time >= pd.Timestamp("2024-07-01", tz="UTC")]),
    ]:
        groups[name] = {
            "units": len(subset),
            "positive_unit_labels": int(subset.net_label_per_margin.gt(0).sum()),
            **{
                f"mean_{field}": float(subset[field].mean())
                for field in [
                    "forecast_carry_per_margin",
                    "price_profit_per_margin",
                    "funding_per_margin",
                    "fees_per_margin",
                    "net_label_per_margin",
                ]
            },
        }
    save(
        output / "result.json",
        {
            "plan_sha256": sha(plan_path),
            "groups": groups,
            "decimal_labels_checked": len(errors),
            "maximum_decimal_error": max(errors),
            "whole_economic_accounts_computed": False,
            "orders_enabled": False,
            "production_eligible": False,
            "scope": plan["scope"],
        },
    )
    print({"groups": groups, "maximum_decimal_error": max(errors)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.plan, args.output)
