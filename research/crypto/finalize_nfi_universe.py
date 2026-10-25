"""Verify pre-2024 warmup calendars before finalizing the ranked author universe."""

import argparse
from pathlib import Path

from freqtrade.research.data import read, save, sha
from research.crypto.archive_history import daily_history


def finalize(provisional: Path, cache: Path, template: Path, output: Path, next_plan: Path):
    output.mkdir(parents=True, exist_ok=False)
    ranked = read(provisional / "ranked.json")
    selected, excluded = [], []
    for row in ranked:
        sources = []
        try:
            frame, sources = daily_history(row["symbol"], "2021-10-01", "2024-01-01", cache)
        except ValueError as error:
            excluded.append(
                {
                    "symbol": row["symbol"],
                    "reason": str(error),
                    "information_period": "2021-10 through2023-12",
                    "sources": sources,
                }
            )
            continue
        except RuntimeError as error:
            if "404" not in str(error):
                save(
                    output / "transient_failure.json",
                    {"symbol": row["symbol"], "error": str(error)},
                )
                raise
            excluded.append(
                {
                    "symbol": row["symbol"],
                    "reason": "official warmup archive unavailable",
                    "information_period": "2021-10 through2023-12",
                    "error": str(error),
                }
            )
            continue
        selected.append(dict(row, warmup_days=len(frame), warmup_sources=sources))
        print(f"Eligible {len(selected)}/40: {row['symbol']}", flush=True)
        if len(selected) == 40:
            break
    save(output / "selected.json", selected)
    save(output / "excluded.json", excluded)
    if len(selected) != 40:
        raise ValueError("Cannot satisfy the fixed pool size and historical coverage")
    result = {
        "symbols": [r["symbol"] for r in selected],
        "pairs": [r["pair"] for r in selected],
        "provisional_result_sha256": sha(provisional / "result.json"),
        "selected_sources_sha256": sha(output / "selected.json"),
        "excluded_sha256": sha(output / "excluded.json"),
        "warmup_calendar": "2021-10-01 inclusive to2024-01-01 exclusive; all actual days",
        "exclusion_uses_post2023_strategy_results": False,
        "survivorship_bias_possible": True,
        "orders_enabled": False,
    }
    save(output / "result.json", result)
    plan = read(template)
    plan.update(
        symbols=result["symbols"],
        universe_result_sha256=sha(output / "result.json"),
        dataset=str(output.parent / "nfi40_data_v002"),
        data_receipts_sha256="PENDING_DATA_PREPARATION",
    )
    plan["scope_limitations"].append(
        "provisional onboardDate did not prove uninterrupted warmup; "
        "final pool requires actual pre2024 daily calendars"
    )
    save(next_plan, plan)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provisional", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--next-plan", type=Path, required=True)
    args = parser.parse_args()
    print(
        finalize(
            args.provisional.resolve(),
            args.cache.resolve(),
            args.template.resolve(),
            args.output.resolve(),
            args.next_plan.resolve(),
        )
    )
