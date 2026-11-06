"""Real official backtests over synthetic, rule-consistent BTC/ETH prices."""
import math
from pathlib import Path
import tempfile
import unittest
import numpy as np
import pandas as pd
from native_factory import build
from candle_model import CandleModel
from freqtrade.configuration import TimeRange
from freqtrade.persistence import LocalTrade
from reconstruct_account import reconstruct

ROOT=Path(__file__).resolve().parents[3]

class FuturesIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='futures-factor-native-')
        self.output=Path(self.temp.name);self.account=None;self.exchange=None
        self.start=pd.Timestamp('2025-01-10',tz='UTC');self.end=self.start+pd.Timedelta(days=2)

    def tearDown(self):
        if self.account is not None:self.account.cleanup()
        if self.exchange is not None:self.exchange.close()
        LocalTrade.reset_trades();self.temp.cleanup()

    def run_native(self,pair,strategy='F2FuturesCandleCost'):
        a,e=build(self.output,strategy,'futures','base',self.start,self.end,self.output,
            ROOT/'research/crypto/strategy_comparison_wide_20261105/public-market-rules-40.json')
        self.account,self.exchange=a,e
        dates=pd.date_range(self.start-pd.Timedelta(days=32),self.end,freq='5min')
        dates=dates[dates<self.end]
        values=np.linspace(1002.,1000.,len(dates))
        values[dates>=self.start+pd.Timedelta(hours=4)]=1003.
        noisy=dates>=self.start+pd.Timedelta(hours=12)
        values[noisy]=1003.*(1+np.array([.01,-.01,0.,0.])[dates[noisy].hour%4])
        rates=pd.DataFrame(dict(date=pd.date_range(self.start,self.end,freq='8h'),open=.0001))
        hourly={};prices={};primary={};marks={};funding_units={}
        pairs=a.config['exchange']['pair_whitelist']
        for p in pairs:
            v=values if p==pair else np.full(len(dates),1000.)
            if strategy=='F1FuturesCandleCost':
                v=1000.*np.exp(np.arange(len(dates))*(.000002 if p==pair else -.000001))
            frame=pd.DataFrame(dict(date=dates,open=v,high=v*1.0001,low=v*.9999,close=v,volume=1e8))
            indexed=frame.set_index('date')
            hours=indexed.resample('h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).reset_index()
            hourly[p]=hours;prices[p]=indexed.open;marks[p]=indexed.open.copy()
            primary[p]=frame if strategy=='F1FuturesCandleCost' else indexed.resample('4h').agg(
                {'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).reset_index()
            if strategy!='F1FuturesCandleCost':a.detail_data[p]=frame
            a.futures_data[p]=e.combine_funding_and_mark(rates,hours[['date','open']])
            funding_units[p]=hours.set_index('date').open.reindex(rates.date).dropna()*.0001
        limits=e.markets[pair]['limits']['price']
        self.assertTrue((prices[pair]>=limits['min']).all())
        self.assertTrue((prices[pair]<=limits['max']).all())
        a.funding_fee_timeframe_secs=8*3600
        model=CandleModel(a,'futures','base',self.start,self.end,hourly,prices,marks=marks)
        a.strategylist[0].cost_model=model;a.strategy=a.strategylist[0]
        a.dataprovider.historic_ohlcv=lambda pair,timeframe,candle_type=None:hourly[pair].copy()
        model.attach()
        a.backtest_one_strategy(a.strategy,primary,TimeRange.parse_timerange(
            f'{int(self.start.timestamp())}-{int(self.end.timestamp())-1}'))
        self.assertGreater(len(LocalTrade.bt_trades),0);self.assertIsNone(model.error)
        self.assertEqual(len(model.rows),576)
        self.assertTrue(all(t.leverage==1 and not t.is_short for t in LocalTrade.bt_trades))
        self.assertTrue(all(t.open_date_utc.minute==5 for t in LocalTrade.bt_trades))
        self.assertTrue(all(self.start<=t.open_date_utc<self.end for t in LocalTrade.bt_trades))
        expected=0.
        for trade in LocalTrade.bt_trades:
            for when in pd.date_range(self.start,self.end,freq='8h',inclusive='left'):
                held=math.fsum(o.filled*(1 if o.ft_order_side=='buy' else -1)
                    for o in trade.orders if o.filled and o.order_filled_utc<when)
                if abs(held)<1e-9:held=0.
                self.assertGreaterEqual(held,0.)
                expected-=held*float(marks[trade.pair].loc[when])*.0001
        actual=math.fsum(t.funding_fees or 0. for t in LocalTrade.bt_trades)
        self.assertLess(actual,0.);self.assertAlmostEqual(actual,expected,places=8)
        curve=pd.DataFrame(model.rows).set_index('date')
        reconstructed=reconstruct([t.to_json(minified=True) for t in LocalTrade.bt_trades],curve.index,marks,funding_units)
        self.assertLess(float(np.max(np.abs(reconstructed['equity']-curve.equity.to_numpy()))),1e-6)
        self.assertLess(float(np.max(np.abs(reconstructed['gross']-curve.gross.to_numpy()))),1e-6)
        return model

    def test_btc_untradable_remainder_becomes_native_full_exit(self):
        model=self.run_native('BTC/USDT:USDT')
        self.assertTrue(model.daily)
        trades=[t for t in LocalTrade.bt_trades if t.exit_reason=='RD1_minimum_full_exit']
        self.assertTrue(trades)
        self.assertTrue(all(t.nr_of_successful_entries==1 and t.nr_of_successful_exits==1 for t in trades))

    def test_eth_tradable_remainder_is_native_partial_exit(self):
        self.run_native('ETH/USDT:USDT')
        orders=[o for t in LocalTrade.bt_trades for o in t.orders if o.filled and o.ft_order_tag=='RD1_candle_reduction']
        self.assertTrue(orders)

    def test_f1_native_daily_entries_and_nonzero_funding(self):
        self.run_native('BTC/USDT:USDT','F1FuturesCandleCost')
        self.assertTrue(all(t.open_date_utc.hour==0 for t in LocalTrade.bt_trades))

if __name__=='__main__':unittest.main()
