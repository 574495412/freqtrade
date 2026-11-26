"""Reuse the deployed selector's structural ranking on frozen historical inputs."""

from datetime import UTC, datetime
import re

import numpy as np
import rapidjson

from deploy.nfi80_selected.light_selector import DAY_MS, structural_selection
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import ROOT, checked, read, receipt, save

from .register import HERE


def policy_for(plan):
    policy_path = ROOT / "deploy/nfi80_selected/policy.json"
    policy = read(policy_path)
    projection = read(ROOT / "deploy/nfi80_selected/input-projection.json")
    assert receipt(policy_path)["sha256"] == projection["policy_sha256"]
    blacklist_path = checked(policy["blacklist_source"])
    patterns = rapidjson.loads(blacklist_path.read_text(), parse_mode=3)["exchange"]["pair_blacklist"]
    candidates = set(policy["candidates"])
    removed = {pair for pair in candidates
               if any(re.fullmatch(pattern, pair, flags=re.IGNORECASE) for pattern in patterns)}
    filtered = sorted(candidates - removed)
    schedule = read(checked(plan["historical_schedule"]))
    assert filtered == sorted(schedule["daily_sources"])
    return policy_path, blacklist_path, filtered, removed, schedule


def daily_series(rows):
    opens = np.asarray([int(row[0]) for row in rows], dtype="int64")
    closes = np.asarray([int(row[6]) for row in rows], dtype="int64")
    turnover = np.asarray([float(row[7]) for row in rows])
    assert len(opens) and np.all(np.diff(opens) == DAY_MS)
    assert (opens % DAY_MS == 0).all() and np.all(closes == opens + DAY_MS - 1)
    assert np.isfinite(turnover).all() and np.all(turnover >= 0)
    return opens, closes, turnover


def ranking(series, decision_ms):
    summaries = {}
    for pair, (opens, closes, turnover) in series.items():
        index = int(np.searchsorted(closes, decision_ms)) - 1
        if index < 0 or opens[index] != decision_ms - DAY_MS:
            continue
        summaries[pair] = {
            "count": min(index + 1, 65),
            "date_ms": int(opens[index]),
            "close_ms": int(closes[index]),
            "quote_volume": float(turnover[index]),
        }
    row = structural_selection(summaries, (), decision_ms)
    assert row["admitted"] and len(set(row["pairs"])) == 80
    return {
        "pairs": row["pairs"], "latest_input_close": row["latest_input_close"],
        "ranked_quote_volume": row["ranked_quote_volume"],
        "eligible_count": row["eligible_after_risk"],
    }


def preflight():
    plan = read(HERE / "plan.json")
    policy, blacklist, pairs, removed, schedule = policy_for(plan)
    sources = schedule["daily_sources"]
    frames = {pair: daily_series(read(checked(sources[pair]))) for pair in pairs}
    for label, reference in schedule["days"].items():
        decision_ms = int(datetime.fromisoformat(label).replace(tzinfo=UTC).timestamp() * 1000)
        rebuilt = ranking(frames, decision_ms)
        assert rebuilt["pairs"] == reference["pairs"], label
        assert rebuilt["ranked_quote_volume"] == reference["ranked_quote_volume"], label
        assert rebuilt["eligible_count"] == reference["eligible_count"], label
        assert datetime.fromisoformat(rebuilt["latest_input_close"]) == datetime.fromisoformat(
            reference["latest_input_close"]
        ), label
    proof = {
        "deployed_policy": receipt(policy), "blacklist_source": receipt(blacklist),
        "historical_schedule": plan["historical_schedule"],
        "deployed_candidates": len(pairs) + len(removed),
        "excluded_by_pinned_blacklist": len(removed),
        "matching_eligible_pairs": len(pairs),
        "exact_daily_rank_decisions": len(schedule["days"]),
        "orders_enabled": False,
    }
    save(HERE / "holdout_schedule_preflight.json", proof)
    print(proof)


if __name__ == "__main__":
    preflight()
