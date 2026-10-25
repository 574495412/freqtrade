"""A relaxed sleeve objective must not silently remove evidence or economic gates."""

import json

import pytest

from research.crypto.select_integration_candidate import COSTS, PHASES, inventory


def accounts(root):
    for phase in PHASES:
        for cost in COSTS:
            folder = root / "run" / phase / "Candidate" / f"cost_{cost}bps"
            folder.mkdir(parents=True)
            result = {
                "candidate": "Candidate",
                "phase": phase,
                "extra_cost_bps_per_side": cost,
                "start": "2024-01-01",
                "end": "2025-01-01",
                "net_return": 0.1,
                "closed_trades": 60,
                "win_rate": 0.55,
                "hourly_observed_drawdown": 0.05,
                "economic_audit": {"passed": True},
                "joint_admission": {"passed": False},
                "small_net_profit_usdt": -1.0,
            }
            (folder / "result.json").write_text(json.dumps(result))


def test_whole_account_selection_does_not_require_positive_sleeves(tmp_path):
    accounts(tmp_path)
    assert inventory(tmp_path)[0]["whole_account_historical_screen_passed"]


@pytest.mark.parametrize("change", ["missing", "failed", "unavailable"])
def test_incomplete_or_failed_economics_still_rejected(tmp_path, change):
    accounts(tmp_path)
    path = tmp_path / "run/holdout/Candidate/cost_10bps/result.json"
    if change == "missing":
        path.unlink()
    else:
        result = json.loads(path.read_text())
        result["economic_audit"] = {} if change == "unavailable" else {"passed": False}
        path.write_text(json.dumps(result))
    assert not inventory(tmp_path)[0]["whole_account_historical_screen_passed"]
