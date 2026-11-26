"""Local OHLCV namespace adapter for the unchanged GPL3 Heracles source.

Native price-precision helper counts can be nullable. The source ta.dropna
otherwise treats them as price eligibility and deletes genuine source bars.
Keep those engine fields intact outside the source's six-column OHLCV input.
"""

import numpy as np
from Heracles import Heracles

from freqtrade.enums import RunMode


class HeraclesNative(Heracles):
    def populate_indicators(self,dataframe,metadata):
        if (self.config.get('research_heracles_ohlcv_adapter') is not True
                or self.config.get('runmode') != RunMode.BACKTEST
                or self.config.get('dry_run') is not True):
            raise ValueError('Explicit BACKTEST source OHLCV adapter required')
        columns=['date','open','high','low','close','volume']
        helpers={'open_count','high_count','low_count','close_count','max_count'}
        if not set(columns).issubset(dataframe) or set(dataframe)-set(columns)-helpers:
            raise ValueError('Exact original OHLCV and known native helper namespace required')
        source=dataframe[columns].copy()
        calculated=super().populate_indicators(source,metadata)
        if not calculated.index.equals(dataframe.index) or len(calculated)!=len(dataframe):
            raise ValueError('Original source rejected an OHLCV row;do not compress sessions')
        for column in columns:
            if not np.array_equal(calculated[column].to_numpy(),dataframe[column].to_numpy()):
                raise ValueError('Original source changed native prices or dates')
        result=dataframe.copy()
        for column in ['volatility_kcw','volatility_dcp']:
            result[column]=calculated[column]
        return result
