"""Check full-year 2025 inputs before authorizing any account result."""

import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt, save
from research.crypto.nfi80_online_baseline_20261125_v1.holdout_schedule import (
    daily_series, policy_for, ranking,
)

from .inputs import HERE, OUT, PARENT, milliseconds


def main():
    plan_path = PARENT / "plan.json"
    plan = read(plan_path)
    _, _, eligible, removed, source = policy_for(plan)
    inputs = source["daily_sources"]
    series = {pair: daily_series(read(checked(inputs[pair]))) for pair in eligible}
    start = pd.Timestamp("2025-01-01T00:00:00Z")
    end = pd.Timestamp("2026-01-01T00:00:00Z")
    days = pd.date_range(start, end, freq="D", inclusive="left")
    ranked, missing = {}, {}
    for date in days:
        try:
            ranked[date.date().isoformat()] = ranking(series, milliseconds(date))
        except AssertionError:
            missing[date.date().isoformat()] = "Fewer than 80 full eligible contracts"
    selected = sorted({pair for row in ranked.values() for pair in row["pairs"]})
    receipts = read(checked(plan["data_receipts"]))
    available = {}
    required = ("5m-futures", "5m-mark", "1h-funding_rate")
    for pair in selected:
        stem = pair.replace("/", "_").replace(":", "_")
        for tag in required:
            key = f"futures/{stem}-{tag}.feather"
            if key not in receipts:
                continue
            path = checked({"path": f"{plan['datadir']}/{key}", "sha256": receipts[key]})
            data = pd.read_feather(path, columns=["date"]).date
            available.setdefault(pair, {})[tag] = {
                "first": data.iloc[0].isoformat(), "last": data.iloc[-1].isoformat(),
                "records_in_year": int(data.between(start, end, inclusive="left").sum()),
            }
    OUT.mkdir(parents=True, exist_ok=True)
    schedule_path = OUT / "schedule_2025.json"
    save(schedule_path, {"days": ranked, "union": selected, "daily_sources": inputs})
    report = {
        "start": start.isoformat(), "end_exclusive": end.isoformat(), "parent": receipt(plan_path),
        "candidate_count": len(eligible), "blacklist_count": len(removed),
        "target_days": len(days), "rankable_days": len(ranked), "non_rankable_days": missing,
        "selected_contracts": len(selected),
        "max_ranked_daily_eligible": max((row["eligible_count"] for row in ranked.values()), default=0),
        "first_rankable_day": min(ranked, default=None),
        "schedule": receipt(schedule_path),
        "replay_tags_all_cover_start": sum(
            all(tag in available.get(pair, {})
                and pd.Timestamp(available[pair][tag]["first"]) <= start for tag in required)
            for pair in selected
        ),
        "coverage_by_contract": available, "execution_coverage_admitted": False, "orders_enabled": False,
    }
    save(HERE / "2025_availability.json", report)
    print({key: value for key, value in report.items()
           if key not in ("coverage_by_contract", "non_rankable_days")}, flush=True)


if __name__ == "__main__":
    main()
