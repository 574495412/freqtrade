"""Daily callback scheduling through native strategy attributes; same orders.

The baseline callback returns None at every non01 hour. Disable the native
position-adjustment call for those hours, retaining native safety wrapping and
all detail-bar stop/exit/liquidation processing. No financial core is patched.
"""
import pandas as pd

from TSMomPortfolio import TSMomPortfolio


class ClockedPortfolio(TSMomPortfolio):
    def bot_loop_start(self, current_time, **kwargs):
        self.position_adjustment_enable = pd.Timestamp(current_time).hour == 1
        return super().bot_loop_start(current_time, **kwargs)


class ClockedCovarianceRiskTS(ClockedPortfolio):
    allocation_mode = 'covariance'


class ClockedDiagonalRiskTS(ClockedPortfolio):
    allocation_mode = 'diagonal'


class ClockedGrossOneRiskTS(ClockedPortfolio):
    allocation_mode = 'gross_one'
