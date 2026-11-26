"""Local option entry boundary;reject before native account construction."""

from freqtrade.data.inverse_option_contracts import require_complete_option_account_support
from freqtrade.optimize.backtesting import Backtesting


class OptionBacktesting(Backtesting):
    def __init__(self, config, exchange=None, *, progress_callback=None):
        require_complete_option_account_support()
