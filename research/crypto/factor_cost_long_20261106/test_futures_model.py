"""One synthetic native futures integration needed before futures financial runs."""
from pathlib import Path
import sys
import unittest
import math
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'factor_cost_scenario_20261106'))
from test_cost_model import CostModelTests as SpotFixture
from candle_model import CandleModel,ROOT
from future_factory import build
from freqtrade.persistence import LocalTrade

class FuturesCostModelTests(unittest.TestCase):
    setUp=SpotFixture.setUp
    tearDown=SpotFixture.tearDown

    def setup_model(self,strategy='F2CandleCost'):
        metadata=ROOT/'research/crypto/strategy_comparison_wide_20261105/public-market-rules-40.json'
        account,exchange=build(self.root,strategy,'futures','base',self.start,self.end,self.root,metadata)
        self.account,self.exchange=account,exchange
        dates=pd.date_range(self.start-pd.Timedelta(days=9),self.end,freq='5min')
        hours=pd.date_range(self.start-pd.Timedelta(days=32),self.end,freq='h')
        rates=pd.DataFrame(dict(date=pd.date_range(self.start,self.end,freq='8h'),open=.0001))
        marks=pd.DataFrame(dict(date=hours,open=10.))
        data={};hourly={};prices={}
        for pair in account.config['exchange']['pair_whitelist']:
            data[pair]=pd.DataFrame(dict(date=dates,open=10.,high=10.001,low=9.999,close=10.,volume=1e8))
            hourly[pair]=pd.DataFrame(dict(date=hours,open=10.,high=10.001,low=9.999,close=10.,volume=1e8))
            prices[pair]=data[pair].set_index('date').open
            account.futures_data[pair]=exchange.combine_funding_and_mark(rates,marks)
        account.funding_fee_timeframe_secs=8*3600
        model=CandleModel(account,'futures','base',self.start,self.end,hourly,prices,marks=prices)
        account.strategylist[0].cost_model=model;account.strategy=account.strategylist[0]
        account.dataprovider.historic_ohlcv=lambda pair,timeframe,candle_type=None:hourly[pair].copy()
        model.attach()
        return model,data

    def test_native_futures_f2_reduction_and_funding_from_synthetic_inventory(self):
        try:
            SpotFixture.test_actual_f2_four_hour_signals_five_minute_entries_and_daily_native_reduction(self)
        except AssertionError:
            print('Synthetic native fills:',[(t.pair,t.stake_amount,t.funding_fees,
                [(o.ft_order_tag,o.filled,o.safe_price) for o in t.orders]) for t in LocalTrade.bt_trades])
            print('Synthetic daily targets:',self.account.strategy.cost_model.daily)
            raise
        expected=0.
        for trade in LocalTrade.bt_trades:
            orders=[o for o in trade.orders if o.filled]
            for when in pd.date_range(self.start,self.end,freq='8h'):
                held=math.fsum(o.filled*(1 if o.ft_order_side=='buy' else -1)
                    for o in orders if o.order_filled_utc<when)
                if abs(held)<1e-9:held=0.
                self.assertGreaterEqual(held,0.)
                expected-=held*10.*.0001
        actual=math.fsum(t.funding_fees or 0. for t in LocalTrade.bt_trades)
        self.assertLess(actual,0.)
        self.assertAlmostEqual(actual,expected,places=8)
        self.assertTrue(all(t.leverage==1 and not t.is_short for t in LocalTrade.bt_trades))

if __name__=='__main__':unittest.main(defaultTest='FuturesCostModelTests')
