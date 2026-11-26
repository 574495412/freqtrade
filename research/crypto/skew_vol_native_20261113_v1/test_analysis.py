# ruff: noqa: S101 - pytest assertions
import pandas as pd
import pytest

from research.crypto.skew_vol_native_20261113_v1.analyze import (
    funding_limit_exposures,
    monthly_budget_audit,
)


def test_actual_monthly_budget_uses_flat_native_cash():
    frame = pd.DataFrame(
        {
            "date": [pd.Timestamp("2025-01-02T00:00Z")],
            "open_trades": [0],
            "hourly_mark_equity": [1000.0],
        }
    )
    assert monthly_budget_audit({"monthly_budgets": {"2025-01": 49.5}}, frame)["passed"]
    with pytest.raises(ValueError):
        monthly_budget_audit({"monthly_budgets": {"2025-01": 50.0}}, frame)
    frame["open_trades"] = 1
    with pytest.raises(ValueError):
        monthly_budget_audit({"monthly_budgets": {"2025-01": 49.5}}, frame)


def test_missing_public_funding_slot_remains_unknown_for_actual_held_position():
    gap = {
        "pair": "AXS/USDT:USDT",
        "expected_slot": "2026-06-24T04:00Z",
        "status": "UNRESOLVED_EXPECTED_4H_SLOT",
    }
    trade = {
        "pair": gap["pair"],
        "open_timestamp": pd.Timestamp("2026-06-02T00:00Z").timestamp() * 1000,
        "close_timestamp": pd.Timestamp("2026-07-01T00:00Z").timestamp() * 1000,
        "is_short": True,
        "open_date": "2026-06-02T00:00Z",
        "close_date": "2026-07-01T00:00Z",
    }
    rows = funding_limit_exposures({"trades": [trade]}, {"expected_unresolved_slots": [gap]})
    assert len(rows) == 1
    assert rows[0]["status"] == gap["status"]
    assert not rows[0]["missing_rate_assumed_zero"]
