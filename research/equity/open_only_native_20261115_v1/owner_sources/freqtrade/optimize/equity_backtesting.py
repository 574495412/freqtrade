"""Local stock backtest entry point; reject until a complete account exists.

Explicit rejection precedes native construction, exchange initialization, wallets,
orders or result recording. The ordinary crypto Backtesting class is unchanged.
"""

from freqtrade.data.history.equity import require_complete_equity_account_support
from freqtrade.optimize.backtesting import Backtesting


class EquityBacktesting(Backtesting):
    """Owning extension boundary for future stock-account implementation."""

    def __init__(self, config, exchange=None, *, progress_callback=None):
        require_complete_equity_account_support()
