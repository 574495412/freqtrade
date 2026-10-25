"""Check unit labels against raw prices and the frozen native funding calculator."""

import argparse
import math
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from freqtrade.exchange.exchange import Exchange
from freqtrade.research.data import read, save, sha


def audit(plan_path, folder):  # noqa: C901 - independent raw chronology and native cash checks
    plan, result = read(plan_path), read(folder / "result.json")
    if result["plan_sha256"] != sha(plan_path):
        raise ValueError("Label plan changed")
    dataset, cache = Path(plan["dataset"]), {}
    hashes = read(dataset / "receipts.json")
    labels = pd.read_parquet(folder / "unit_labels.parquet").set_index(["symbol", "entry"])
    ledgers = read(folder / "unit_ledgers.json")
    native = SimpleNamespace(_add_funding_columns=Exchange._add_funding_columns)
    errors, fee_errors, funding_errors, observed = [], [], [], 0

    def load(symbol):
        if symbol not in cache:
            stem, frames = f"{symbol[:-4]}_USDT_USDT", []
            for suffix in ["5m-futures", "1h-mark", "1h-funding_rate"]:
                relative = f"candles/futures/{stem}-{suffix}.feather"
                path = dataset / relative
                if sha(path) != hashes[relative]:
                    raise ValueError("Raw input changed")
                frames.append(pd.read_feather(path))
            candles, mark, funding = frames
            combined = pd.merge_asof(
                funding.rename(columns={"funding_rate": "open_fund"}),
                mark[["date", "open"]].rename(columns={"date": "mark_date", "open": "open_mark"}),
                left_on="date",
                right_on="mark_date",
                direction="backward",
                tolerance=pd.Timedelta(hours=1),
            )
            if combined.open_mark.isna().any() or combined.mark_date.gt(combined.date).any():
                raise ValueError("Funding mark missing or from the future")
            cache[symbol] = candles.set_index("date"), combined
        return cache[symbol]

    for unit in ledgers:
        begin, finish = pd.Timestamp(unit["entry"]), pd.Timestamp(unit["exit"])
        if finish - begin != pd.Timedelta(days=1):
            raise ValueError("Horizon changed")
        profits, fees, credits_ = [], [], []
        for leg in unit["legs"]:
            candles, combined = load(leg["symbol"])
            first, final = float(candles.loc[begin, "open"]), float(candles.loc[finish, "open"])
            if first != leg["entry_price"] or final != leg["exit_price"]:
                raise ValueError("Execution price mismatch")
            if combined.date.eq(finish).any():
                raise ValueError("Exclusive label/inclusive native endpoint requires disposition")
            quantity = 1 / first
            funding = Exchange.calculate_funding_fees(
                native,
                combined.copy(),
                quantity,
                leg["side"] < 0,
                begin.to_pydatetime(),
                finish.to_pydatetime(),
            )
            selected = combined.loc[combined.date.ge(begin) & combined.date.le(finish)]
            if list(selected.date) != [pd.Timestamp(e["date"]) for e in leg["funding_events"]]:
                raise ValueError("Funding event membership changed")
            observed += len(selected)
            profits.append(leg["side"] * (final / first - 1))
            fees.append((1 + final / first) * plan["fee_per_side_with_cost"])
            credits_.append(funding)
        label = labels.loc[(unit["symbol"], begin)]
        expected = math.fsum(profits) + math.fsum(credits_) - math.fsum(fees)
        errors.append(abs(expected - label.net_label_per_margin))
        funding_errors.append(abs(math.fsum(credits_) - label.funding_per_margin))
        fee_errors.append(abs(math.fsum(fees) - label.fees_per_margin))
    if max(errors + funding_errors + fee_errors) > 1e-12 or len(ledgers) != len(labels):
        raise ValueError("Native unit cash check failed")
    return {
        "passed": True,
        "units_checked": len(labels),
        "funding_events_checked": observed,
        "maximum_net_label_error": max(errors),
        "maximum_funding_error": max(funding_errors),
        "maximum_fee_error": max(fee_errors),
        "label_result_sha256": sha(folder / "result.json"),
        "audit_source_sha256": sha(Path(__file__)),
        "native_exchange_source_sha256": sha(Path("freqtrade/exchange/exchange.py")),
        "scope": "Raw price/event membership and native funding arithmetic. No portfolio, "
        "liquidation or stop simulation; these labels are not continuous-account profits.",
        "orders_enabled": False,
        "production_eligible": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--folder", type=Path, required=True)
    args = parser.parse_args()
    path = args.folder / "independent_native_audit.json"
    if path.exists():
        raise ValueError("Preserve previous audit")
    result = audit(args.plan, args.folder)
    save(path, result)
    print(
        {
            k: result[k]
            for k in [
                "passed",
                "units_checked",
                "funding_events_checked",
                "maximum_net_label_error",
            ]
        }
    )
