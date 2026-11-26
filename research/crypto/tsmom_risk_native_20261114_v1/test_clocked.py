import sys
from pathlib import Path

import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, '/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')
sys.path.insert(0, str(HERE))
from ClockedPortfolio import ClockedCovarianceRiskTS, ClockedDiagonalRiskTS, ClockedGrossOneRiskTS
from TSMomPortfolio import CovarianceRiskTS


@pytest.mark.parametrize('owner_type', [ClockedCovarianceRiskTS, ClockedDiagonalRiskTS, ClockedGrossOneRiskTS])
def test_native_adjustment_flag_matches_existing_no_order_hours(owner_type):
    owner = owner_type({})
    original = CovarianceRiskTS({})
    # Every original callback outside01 returns before consulting trades/prices
    # or writing adjustment state. The native flag skips exactly those hours.
    for stamp in pd.date_range('2025-01-01T00:00Z', periods=24*12, freq='5min'):
        if stamp.hour != 1:
            assert original.context(stamp) is None
            assert original.adjust_trade_position(None, stamp, 100., 0., 1., 100.) is None
        owner.context = lambda _: None
        owner.bot_loop_start(stamp)
        assert owner.position_adjustment_enable == (stamp.hour == 1)
