"""Full native trade/order and NAV comparisons across the market execution overlay."""

import argparse

import pandas as pd

from freqtrade.research.data import save, sha
from research.crypto.run_candidate import native_stats


def compare(before, after):
    old, new = native_stats(before)["trades"], native_stats(after)["trades"]
    changes = []
    if len(old) != len(new):
        changes.append({"kind": "campaign_count", "before": len(old), "after": len(new)})
    for index, (a, b) in enumerate(zip(old, new, strict=False)):
        different = [key for key in set(a) | set(b) if a.get(key) != b.get(key)]
        if different:
            changes.append({"campaign": index, "different_fields": sorted(different)})
    a, b = [pd.read_parquet(p / "hourly_equity.parquet") for p in [before, after]]
    curve_equal = a.equals(b)
    if not curve_equal:
        changes.append(
            {"kind": "marked_equity_or_inventory", "before_rows": len(a), "after_rows": len(b)}
        )
    return {
        "passed": not changes,
        "before": str(before),
        "after": str(after),
        "native_campaigns_before": len(old),
        "native_campaigns_after": len(new),
        "full_trade_and_order_json_equal": old == new,
        "full_hourly_curve_equal": curve_equal,
        "changes": changes[:20],
        "source_sha256": {
            str(p): sha(p)
            for folder in [before, after]
            for p in [
                folder / "result.json",
                folder / "hourly_equity.parquet",
                next(folder.glob("*.zip")),
            ]
        },
        "scope": "Historical native execution parity only; exchange stop acceptance not verified",
    }


if __name__ == "__main__":
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Preserve previous parity evidence")
    result = compare(args.before.resolve(), args.after.resolve())
    save(args.output, result)
    print({"passed": result["passed"], "changes": result["changes"]})
