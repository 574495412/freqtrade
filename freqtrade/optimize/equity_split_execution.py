"""Explicit native construction seam; no replacement matching or fills."""

from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.equity_split_wallets import EquitySplitWallets
from freqtrade.persistence.equity_trade import EquitySplitTrade


class EquitySplitTradeMixin:
    def _create_trade(self, **kwargs):
        if not isinstance(self.wallets, EquitySplitWallets):
            raise EquityAdmissionError("Split native trade creation requires its bound wallet")
        self._require_open_only()
        return EquitySplitTrade(splits=self.wallets.splits, **kwargs)
