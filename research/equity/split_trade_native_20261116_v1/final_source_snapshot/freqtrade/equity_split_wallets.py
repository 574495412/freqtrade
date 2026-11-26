"""Implemented integral share events through actual native wallets and trades."""

from freqtrade.data.equity_splits import ShareSplitContract
from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.equity_dividend_wallets import EquityDividendWallets
from freqtrade.persistence import Trade
from freqtrade.persistence.equity_trade import EquitySplitTrade


class EquitySplitWallets(EquityDividendWallets):
    def __init__(self, config, exchange, *, fees, dividends, splits, asof):
        if (
            not isinstance(splits, ShareSplitContract)
            or splits.cash != fees.cash
            or splits.pairs != dividends.pairs
            or asof != splits.coverage_start
        ):
            raise EquityAdmissionError("Matching complete split/cash/dividend coverage required")
        self.splits = self._bound_splits = splits
        super().__init__(config, exchange, fees=fees, dividends=dividends, asof=asof)

    def quantity_asof(self, pair, quantity, filled_at, asof):
        return self.splits.quantity(pair, quantity, filled_at, asof)

    def _update_dry(self):
        if self.splits is not self._bound_splits or self.splits.cash != self.contract:
            raise EquityAdmissionError("Implemented split account contract cannot switch")
        self.splits.require_clock(self.asof)
        trades = list(Trade.get_trades_proxy())
        if any(not isinstance(t, EquitySplitTrade) or t.splits is not self.splits for t in trades):
            raise EquityAdmissionError("All actual native trades must share this split contract")
        previous = [(trade, trade.__dict__.copy()) for trade in trades]
        try:
            for trade in trades:
                trade.advance_units(self.asof)
            super()._update_dry()
        except Exception:
            for trade, state in previous:
                trade.__dict__.clear()
                trade.__dict__.update(state)
            raise
