from copy import deepcopy
from unittest.mock import patch

import pytest

from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.optimize.equity_backtesting import EquityBacktesting


def test_stock_account_rejected_before_native_mutation():
    config = {"equity_market": "XNYS", "dry_run_wallet": 10000}
    original = deepcopy(config)
    with patch("freqtrade.optimize.backtesting.Backtesting.__init__") as native:
        with pytest.raises(EquityAdmissionError, match="not integrated"):
            EquityBacktesting(config)
        native.assert_not_called()
    assert config == original
