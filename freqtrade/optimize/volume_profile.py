"""Local BACKTEST-only funded-state signal classification on native Backtesting.

Classify before native detail-cache construction: otherwise a flat dynamic entry
would incorrectly see a full15m candle instead of native5m detail. Every wallet,
fill, fee, funding, margin and exit method remains the native implementation.
"""

from freqtrade.enums import RunMode
from freqtrade.optimize.backtesting import Backtesting, DATE_IDX
from freqtrade.persistence import LocalTrade, Trade


class VolumeProfileBacktesting(Backtesting):
    def check_for_trade_entry(self, row):
        callback = getattr(self.strategy, "native_profile_step", None)
        if callback is None:
            return super().check_for_trade_entry(row)
        if (
            self.config.get("runmode") != RunMode.BACKTEST
            or not self.config.get("dry_run")
            or Trade.use_db
        ):
            raise ValueError("Funded profile hook is BACKTEST only")
        pair = "BTC/USDT:USDT"
        if self.timeframe != "15m" or self.config["exchange"]["pair_whitelist"] != [pair]:
            raise ValueError("Frozen profile clock/pair changed")
        return callback(pair, row[DATE_IDX], bool(LocalTrade.bt_trades_open_pp[pair]), True)
