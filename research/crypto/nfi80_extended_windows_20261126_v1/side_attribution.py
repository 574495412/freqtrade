"""Measure the short sleeve at actual peak-to-trough marks and check replay parity."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from math import fsum, isclose
from pathlib import Path
import re

import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .capacity_2024_drawdown_attribution import worst_drawdown
from .inputs import HERE
from .recent_two_year_attribution import ledger
from .recent_two_year_drawdown_audit import attribution
from .recent_two_year_prefix_audit import BOUNDARY, snapshot_log


def snapshot_before(path, previous=None):
    data = path.read_bytes()
    data = data[:data.rfind(b"\n") + 1]
    if previous is not None:
        assert sha256(data[:previous["observed_bytes"]]).hexdigest() == previous["observed_prefix_sha256"]
    return data, {"path": str(path), "observed_bytes": len(data), "sha256": sha256(data).hexdigest()}


def verify_complete_log_prefix():
    prefix = receipt(HERE / "recent_two_year_8bp_14_strategy_prefix.json")
    earlier = read(checked(prefix))
    annual = Path(earlier["annual_log"]["path"])
    continuous = Path(earlier["continuous_log"]["path"])
    before = read(continuous.parent / "account/replay-clock.json")
    assert pd.Timestamp(before["clock"]) >= BOUNDARY
    original, old_log = snapshot_log(annual)
    replay, current_log = snapshot_log(continuous)
    snapshot_before(annual, earlier["annual_log"])
    snapshot_before(continuous, earlier["continuous_log"])
    timestamp = re.compile(rb"\[(20\d{2}-\d\d-\d\d) ")
    end = len(original)
    assert end > earlier["native_strategy_events_matched"]
    assert replay[:end] == original
    later = [match[1] for line in replay[end:] if (match := timestamp.search(line))]
    assert later and all(clock >= b"2026-01-01" for clock in later)
    annual_stakes, old_stakes = snapshot_before(annual.parent / "account/stake-decisions.jsonl")
    replay_stakes, current_stakes = snapshot_before(continuous.parent / "account/stake-decisions.jsonl")

    def in_2025(data):
        return [line for line in data.splitlines() if pd.Timestamp(json.loads(line)["time"]) < BOUNDARY]

    original_stakes = in_2025(annual_stakes)
    assert len(original_stakes) and len(annual_stakes.splitlines()) == len(original_stakes)
    assert in_2025(replay_stakes) == original_stakes
    return {
        "previous_checkpoint": prefix, "captured_at": datetime.now(timezone.utc).isoformat(),
        "replay_clock": before, "annual_reconciled": earlier["annual_reconciled"],
        "continuous_plan": earlier["continuous_plan"], "annual_log": old_log, "continuous_log": current_log,
        "annual_stake_log": old_stakes, "continuous_stake_log": current_stakes,
        "matched_strategy_messages": end, "matched_stake_callback_rows": len(original_stakes),
        "subsequent_dated_messages": len(later), "subsequent_undated_messages": len(replay) - end - len(later),
        "passed": True, "orders_enabled": False,
    }


def sides(interval):
    results = {
        side: fsum(t["marked_pnl_change_usdt"] for t in interval["campaigns"] if t["side"] == side)
        for side in ("long", "short")
    }
    assert isclose(fsum(results.values()), interval["marked_nav_change_usdt"], abs_tol=3e-5, rel_tol=0)
    start_nav = interval["marked_nav_usdt"][0]
    return {
        "pnl_usdt": results, "fraction_of_peak_nav": {side: pnl / start_nav for side, pnl in results.items()},
        "short_pnl_times_long_loss": results["short"] / -results["long"] if results["long"] < 0 else None,
    }


def drawdowns():
    source = receipt(HERE / "recent_two_year_15bp_drawdown_comparison.json")
    continuous_15 = read(checked(source))["intervals_by_peak_owner"]
    result = {
        "15bp": {"source": source, "windows": {owner: {
            "peak": row["peak"], "trough": row["trough"], "drawdown": row["drawdown"],
            "accounts": {name: sides(c) for name, c in row["accounts"].items()},
        } for owner, row in continuous_15.items()}},
    }
    report_ref = receipt(HERE / "recent_two_year_8bp_12_verified.json")
    report = read(checked(report_ref))["account"]
    wallet = ledger(read(checked(report["proof"])))
    peak, trough, loss = worst_drawdown(wallet["curve"].marked_equity)
    assert isclose(loss, report["periods"]["continuous"]["drawdown"], abs_tol=1e-12, rel_tol=0)
    plan = read(checked(report["registered_plan"]))
    row = attribution(wallet, plan["data_receipts"], peak, trough)
    result["8bp_12"] = {"source": report_ref, "peak": row["peak"], "trough": row["trough"],
                        "drawdown": loss, "accounts": {"12": sides(row)}, "complete_campaigns": row}
    return result


def main():
    source = receipt(Path(__file__))
    target = HERE / "recent_two_year_8bp_14_year_strategy_prefix.json"
    if target.exists():
        prefix = read(target)
        assert prefix["source"] == source and prefix["passed"]
    else:
        prefix = {
            "source": source, **verify_complete_log_prefix(),
            "scope": "2025 strategy and stake callback logs; pending fill and marked equity comparison",
        }
        write_once(target, prefix)
    print("2025 exact prefix", prefix["matched_strategy_messages"], prefix["matched_stake_callback_rows"], flush=True)
    record = {
        "source": source, "drawdowns": drawdowns(),
        "scope": "Additive attribution of actual marked campaign PnL; no counterfactual sizing or order simulation",
        "orders_enabled": False,
    }
    write_once(HERE / "recent_two_year_side_drawdown.json", record)
    print({name: row["accounts"] for name, row in record["drawdowns"].items() if "accounts" in row}, flush=True)


if __name__ == "__main__":
    main()
