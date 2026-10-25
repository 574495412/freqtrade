"""Actual held inventory drives the carry rank buffer; sleeves remain disjoint."""

import pytest

from research.crypto.github_factor_portfolio import rank_buffer_weights


def test_rank_buffer_retains_actual_inventory_inside_keep_band():
    scores = {f"C{i:02d}": 40 - i for i in range(30)}
    held = {"C10": {"side": 1}, "C19": {"side": -1}}
    weights = rank_buffer_weights(scores, held, 0.8, 8, 4)
    assert weights["C10"] > 0 and weights["C19"] < 0
    assert len(weights) == 16
    assert sum(weights.values()) == pytest.approx(0)
    assert sum(abs(v) for v in weights.values()) == pytest.approx(0.8)
    # A stale signal-owned wish is not actual inventory, so it receives no keep privilege.
    without = rank_buffer_weights(scores, {}, 0.8, 8, 4)
    assert "C10" not in without and "C19" not in without


def test_small_pool_does_not_create_overlapping_retention_sleeves():
    scores = {f"C{i:02d}": 30 - i for i in range(20)}
    assert rank_buffer_weights(scores, {}, 0.8, 8, 4) == {}
