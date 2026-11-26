"""Resolve gross bounds using the pinned engine's open-pairs-first iterator."""

import json
from pathlib import Path
import subprocess
import sys

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .inputs import HERE
from .marginal_gross_guard import GROSS_LIMIT


def native_clock_witness(plan):
    engine = Path(plan["engine_source"])
    code = """
from datetime import datetime, timedelta, timezone
import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from freqtrade.optimize.backtesting import Backtesting, LocalTrade
clock = datetime(2025, 1, 1, tzinfo=timezone.utc)
context = SimpleNamespace(timeframe_td=timedelta(minutes=5),
                          _time_generator_det=lambda a,b: [(a, True, False, 0)])
with patch.object(LocalTrade, 'bt_trades_open', [SimpleNamespace(pair='A'), SimpleNamespace(pair='B')]):
    pairs = [row[-1] for row in Backtesting._time_pair_generator_det(context, clock, ['C','B','D','A'])]
assert pairs == ['A','B','C','D']
path = Path(inspect.getfile(Backtesting)).resolve()
assert path == Path(sys.argv[1]) / 'freqtrade/optimize/backtesting.py'
print(json.dumps(pairs))
"""
    order = json.loads(subprocess.check_output([sys.executable, "-B", "-c", code, str(engine)], text=True))
    return {"engine_commit": plan["engine_commit"], "engine_source": str(engine), "pair_order": order,
            "engine_file": receipt(engine / "freqtrade/optimize/backtesting.py")}


def classify(rows):
    revised = []
    for row in rows:
        if row["decision"] != "unresolved":
            revised.append(row)
            continue
        low, high = row["projected_gross_bounds_to_nav"]
        # Existing pairs complete their rows first. Later fills of earlier new pairs
        # finish in those pairs' loops before the target confirmation is reached.
        projected = high + row["included_reductions_usdt"] / row["pre_candle_marked_nav_usdt"]
        assert low - 1e-10 <= projected <= high + 1e-10
        verdict = "unresolved"
        if projected <= GROSS_LIMIT:
            verdict = "would_allow"
        elif row["entry_slot_bounds"][0] > 12:
            verdict = "would_block"
        revised.append({**row, "bounds_decision": row["decision"], "decision": verdict,
                        "native_order_projected_gross_to_nav": projected})
    return revised


def main():
    base = receipt(HERE / "marginal_gross_bounds_result.json")
    original = read(checked(base))
    initial = read(checked(read(checked(original["original"]))["freeze"]))
    stress = read(checked(initial["stress"]))
    historical = read(checked(stress["account_sources"]["14"]["native_plan"]))
    configs = [read(checked(historical["candidates"][0]["config"]))]
    for ref in initial["recent"].values():
        account = read(checked(ref))["accounts"]["14"]
        plan = read(checked(account["registered_plan"]))
        assert plan["engine_source"] == historical["engine_source"] and plan["engine_commit"] == historical["engine_commit"]
        configs.append(read(checked(account["effective_config"])))
    assert all(not cfg.get("timeframe_detail") and not cfg.get("position_stacking") for cfg in configs)
    witness = native_clock_witness(historical)
    stress_cases = classify(original["stress_2024"])
    recent = {fee: classify(rows) for fee, rows in original["recent"].items()}
    stress_pass = any(r["pair"] in ("ETH/USDT:USDT", "ONG/USDT:USDT") and r["decision"] == "would_block"
                      for r in stress_cases)
    winners = {fee: all(r["decision"] in ("outside_rule", "would_allow") for r in rows
                        if r["over_1pct_of_prior_equity"]) for fee, rows in recent.items()}
    write_once(HERE / "marginal_gross_native_order_result.json", {
        "source": receipt(Path(__file__)), "bounds": base, "engine_witness": witness,
        "stress_2024": stress_cases, "near_two_years": recent, "stress_hit": stress_pass,
        "large_winners_retained": winners, "advance": stress_pass and all(winners.values()),
        "orders_enabled": False,
    })
    print(stress_pass, winners, flush=True)


if __name__ == "__main__":
    main()
