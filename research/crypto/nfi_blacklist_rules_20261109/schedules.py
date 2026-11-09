"""Evaluate frozen inputs with the actual native risk filter implementation."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/nfi_blacklist_rules_20261109_v001"
sys.path.insert(0, str(OUT / "engine"))

import pandas as pd  # noqa: E402

from freqtrade.enums import CandleType, RunMode  # noqa: E402
from freqtrade.resolvers import ExchangeResolver  # noqa: E402


def read(p):
    return json.loads(Path(p).read_text())


def save(p, value):
    Path(p).write_text(json.dumps(value, indent=2, default=str) + "\n")


def ref(p):
    return {"path": str(p), "sha256": hashlib.sha256(Path(p).read_bytes()).hexdigest()}


def main():
    if (HERE / "plans.json").exists():
        raise FileExistsError("Policy schedules already frozen")
    meta = read(HERE / "eligibility.json")
    parent = read(
        ROOT / "research/crypto/nfi80_historical_universe_20261109/v003/flatboost-plan.json"
    )
    config = read(parent["candidates"][0]["config"]["path"])
    config.update(runmode=RunMode.BACKTEST, candle_type_def=CandleType.FUTURES)
    exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
    plugin_path = OUT / "engine/freqtrade/plugins/pairlist/HistoricalVolumePairList.py"
    spec = importlib.util.spec_from_file_location("pinned_native_rulebook", plugin_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    daily = {p: ref(OUT / "daily" / (p.split("/")[0] + ".parquet")) for p in meta["candidates"]}
    summaries = {}
    try:
        for variant in ["structural", "native_risk"]:
            payload = {
                "variant": variant,
                "daily_inputs": daily,
                "structural_blacklist": meta["structural_blacklist"],
            }
            book = module.RuleBook(payload, exchange, SimpleNamespace(), config)
            days = {}
            for day in pd.date_range(
                parent["start"], pd.Timestamp(parent["comparison_end"]).normalize(), freq="D"
            ):
                days[day.date().isoformat()] = book.select(day)
                if day.day == 1:
                    print(
                        {
                            "variant": variant,
                            "month": str(day.date()),
                            "eligible": days[day.date().isoformat()]["eligible_after_risk"],
                        },
                        flush=True,
                    )
            union = sorted({p for row in days.values() for p in row["pairs"]})
            result = {**payload, "days": days, "union": union}
            path = OUT / (variant + "-schedule.json")
            save(path, result)
            invalid = {d: r["eligible_after_risk"] for d, r in days.items() if not r["admitted"]}
            summaries[variant] = {
                "schedule": ref(path),
                "union_count": len(union),
                "days": len(days),
                "contract_admitted": not invalid,
                "unsupported_days": invalid,
                "minimum_eligible_after_risk": min(r["eligible_after_risk"] for r in days.values()),
                "membership_additions": sum(
                    len(set(b["pairs"]) - set(a["pairs"]))
                    for a, b in zip(list(days.values())[:-1], list(days.values())[1:], strict=True)
                ),
            }
        save(HERE / "schedule-summary.json", summaries)
    finally:
        exchange.close()
    print(summaries, flush=True)


if __name__ == "__main__":
    main()
