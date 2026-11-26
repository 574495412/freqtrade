"""Calendar seam and complete-day Sharpe checks, independent of strategy code."""
import importlib.util
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_spec = importlib.util.spec_from_file_location('source_account_auditor', Path(__file__).with_name('analyze.py'))
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
metrics = _module.metrics


def test_partial_final_day_affects_return_and_drawdown_but_not_daily_sharpe():
    index = pd.date_range('2025-01-01', periods=72, freq='h', tz='UTC')
    values = np.interp(np.arange(72), [0, 24, 48, 71], [100, 110, 104.5, 90])
    result = metrics(pd.Series(values, index=index))
    expected = np.array([.1, -.05])
    assert result['complete_24h_returns'] == 2
    assert result['daily_nav_sharpe'] == pytest.approx(
        expected.mean() / expected.std(ddof=1) * math.sqrt(365))
    assert result['net_return'] == pytest.approx(-.1)
    assert result['maximum_hourly_mark_drawdown'] == pytest.approx(1 - 90 / 110)


def test_shared_calendar_seam_compounds_to_continuous_account():
    index = pd.date_range('2025-12-31', periods=73, freq='h', tz='UTC')
    nav = pd.Series(np.linspace(100, 83, len(index)), index=index)
    seam = pd.Timestamp('2026-01-01', tz='UTC')
    full, left, right = metrics(nav), metrics(nav.loc[:seam]), metrics(nav.loc[seam:])
    assert (1 + left['net_return']) * (1 + right['net_return']) - 1 == pytest.approx(
        full['net_return'])
    assert left['ending_usdt'] == right['initial_usdt']
    assert full['complete_24h_returns'] == 3
