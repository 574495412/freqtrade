"""Focused model invariants and actual native callback/backtest integration."""
from copy import deepcopy
from datetime import timedelta
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from freqtrade.configuration import TimeRange
from freqtrade.persistence import LocalTrade, Order
from freqtrade.enums import TradingMode
from candle_model import CandleModel, cash_value, ROOT, D
from native_factory import build


class CostModelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='fixed-factor-cost-test-')
        self.root=Path(self.temp.name);self.account=None;self.exchange=None
        self.start=pd.Timestamp('2025-01-10',tz='UTC');self.end=self.start+pd.Timedelta(days=2)

    def tearDown(self):
        if self.account is not None:self.account.cleanup()
        if self.exchange is not None:self.exchange.close()
        LocalTrade.reset_trades();self.temp.cleanup()

    def setup_model(self,strategy='F1CandleCost'):
        metadata=ROOT/'research/crypto/spot_baseline_20261105/ccxt-spot-market-rules.json'
        a,e=build(self.root,strategy,'spot','base',self.start,self.end,self.root,metadata)
        self.account,self.exchange=a,e
        pairs=a.config['exchange']['pair_whitelist']
        dates=pd.date_range(self.start-pd.Timedelta(days=9),self.end,freq='5min')
        hours=pd.date_range(self.start-pd.Timedelta(days=32),self.end,freq='h')
        def bars(ds,price):return pd.DataFrame(dict(date=ds,open=price,high=price*1.001,low=price*.999,close=price,volume=1e8))
        data={};hourly={};prices={}
        for i,p in enumerate(pairs):
            # Three leading synthetic momentum paths, all rising, no signal search.
            hourly[p]=bars(hours,10*np.exp(np.arange(len(hours))*(.0002 if i<3 else -.0001)))
            data[p]=bars(dates,np.full(len(dates),10.))
            prices[p]=data[p].set_index('date').open
        model=CandleModel(a,'spot','base',self.start,self.end,hourly,prices)
        a.strategylist[0].cost_model=model;a.strategy=a.strategylist[0]
        a.dataprovider.historic_ohlcv=lambda pair,timeframe,candle_type=None:hourly[pair].copy()
        model.attach()
        return model,data

    def test_actual_official_backtest_enters_fixed_f1_and_records_unrealized_account(self):
        m,data=self.setup_model()
        self.account.backtest_one_strategy(self.account.strategy,data,
            TimeRange.parse_timerange(f'{int(self.start.timestamp())}-{int(self.end.timestamp())}'))
        result=self.account.all_bt_content['F1CandleCost']['results']
        self.assertGreater(len(result),0)
        self.assertIsNone(m.error)
        self.assertEqual(len(m.rows),576)
        self.assertTrue(all(t.minute==5 and t.hour==0 for t in result.open_date))
        self.assertLess(m.rows[-1]['equity'],1000)  # flat prices still pay fees/exit reserve
        self.assertLessEqual(max(r['gross']/r['equity'] for r in m.rows),.300001)
        self.assertTrue(all(len([o for o in t.orders if o.ft_order_side==t.entry_side and o.filled])==1 for t in LocalTrade.bt_trades))

    def test_absolute_floor_and_hwm_halt_include_floating_loss(self):
        m,_=self.setup_model();t=self.start.to_pydatetime()
        m.update_limits(t,1000,[],{'breaches':[]})
        m.update_limits(t+timedelta(minutes=1),900,[object()],{'breaches':[]})
        self.assertTrue(m.warn)
        m.update_limits(t+timedelta(minutes=5),850,[object()],{'breaches':[]})
        self.assertTrue(m.hard);self.assertTrue(m.exit_all)
        m.update_limits(t+timedelta(minutes=10),1100,[],{'breaches':[]})
        self.assertTrue(m.hard)
        m.hard=False;m.peak=500
        m.update_limits(t+timedelta(minutes=15),500,[],{'breaches':[]})
        self.assertTrue(m.hard)

    def test_daily_anchor_requires_midnight_and_daily_latch_requires_flat_next_day(self):
        m,_=self.setup_model();t=self.start.to_pydatetime()
        with self.assertRaisesRegex(ValueError,'midnight'):m.update_limits(t+timedelta(minutes=5),1000,[],{'breaches':[]})
        m.update_limits(t,1000,[],{'breaches':[]})
        m.update_limits(t+timedelta(minutes=5),980,[object()],{'breaches':[]})
        self.assertTrue(m.exit_all)
        m.update_limits(t+timedelta(days=1),980,[object()],{'breaches':[]})
        self.assertTrue(m.exit_all)
        m.update_limits(t+timedelta(days=1,minutes=5),980,[],{'breaches':[]})
        self.assertFalse(m.exit_all)

    def test_callback_error_cannot_fall_back_to_native_proposed_stake(self):
        m,_=self.setup_model();s=self.account.strategy
        with patch.object(m,'budget',side_effect=ValueError('synthetic input failure')):
            self.assertEqual(s.custom_stake_amount('BTC/USDT',self.start,10,999,5,999),0)
            self.assertFalse(s.confirm_trade_entry('BTC/USDT',50,10,self.start,'long'))
        with self.assertRaisesRegex(ValueError,'synthetic input failure'):m.observe(self.start)

    def test_volume_and_pair_risk_caps_bind_without_fabricating_depth(self):
        m,_=self.setup_model();now=(self.start+pd.Timedelta(minutes=5)).to_pydatetime()
        m.observe(self.start);m.observe(now)
        pair=m.pairs[0];h=self.start-pd.Timedelta(hours=1)
        m.hourly[pair].loc[h,'volume']=10
        self.assertLessEqual(m.budget(pair,now,10,'F1'),.10000001)
        m.hourly[pair].loc[h,'volume']=1e9
        self.assertLessEqual(m.budget(pair,now,10,'F1'),1000*.005/(.05+2*.0013))
        self.assertEqual(m.budget(pair,now+timedelta(minutes=5),10,'F1'),0)

    def test_missing_correlation_uses_one_cluster_and_future_rows_do_not_change_scale(self):
        m,_=self.setup_model('F2CandleCost');now=(self.start+pd.Timedelta(minutes=5)).to_pydatetime()
        pair=m.pairs[0]
        before=m.scale(pair,now)
        f=m.hourly[pair];f.loc[f.index>=self.start,'close']*=100
        self.assertEqual(m.scale(pair,now),before)
        m.hourly[pair]=f.iloc[-10:];m.corr_day=None
        self.assertIsNone(m.correlations(now))
        snap=SimpleNamespace(correlations=None)
        groups,complete=m.clusters(snap)
        self.assertFalse(complete);self.assertEqual(len(set(groups.values())),1)

    def test_f2_daily_cap_is_fixed_once_and_does_not_rescale_remaining_position(self):
        m,_=self.setup_model('F2CandleCost');now=(self.start+pd.Timedelta(minutes=5)).to_pydatetime()
        pair=m.pairs[0];trade=SimpleNamespace(pair=pair,id=1,amount=9.)
        snap=SimpleNamespace(correlations=None,orders=())
        with patch.object(m,'scale',return_value=.5):
            first=m.allocations(now,1000,[trade],{pair:10},snap,{})
        trade.amount=first[1]
        with patch.object(m,'scale',return_value=.1):
            later=m.allocations(now+timedelta(minutes=5),1000,[trade],{pair:10},snap,{})
        self.assertEqual(first,later)
        self.assertAlmostEqual(first[1],1000*.005/(.05+2*.0013)*.5/10)

    def test_partial_exit_cash_value_counts_realized_cash_fees_and_remaining_exit_reserve(self):
        buy=SimpleNamespace(ft_is_open=False,filled=5.,ft_order_side='buy',safe_price=10.)
        sell=SimpleNamespace(ft_is_open=False,filled=2.,ft_order_side='sell',safe_price=11.)
        t=SimpleNamespace(orders=[buy,sell],entry_side='buy',amount=3.,fee_open=.001,fee_close=.001,funding_fees=-.2)
        self.assertAlmostEqual(cash_value(t,9),22+27-50-.05-.022-.027-.2)

    def test_native_strategy_can_only_start_with_attached_backtest_model(self):
        m,_=self.setup_model();s=self.account.strategy
        del s.cost_model
        with self.assertRaisesRegex(RuntimeError,'attachment'):s.bot_start()

    def test_actual_f2_four_hour_signals_five_minute_entries_and_daily_native_reduction(self):
        m,data=self.setup_model('F2CandleCost')
        pair=m.pairs[0];dates=data[pair].date
        values=np.linspace(10.02,10.,len(dates))
        after=dates>=self.start+pd.Timedelta(hours=4)
        values[after]=10.03
        noisy=dates>=self.start+pd.Timedelta(hours=12)
        pattern=np.array([.01,-.01,0.,0.])
        values[noisy]=10.03*(1+pattern[dates[noisy].dt.hour.to_numpy()%4])
        primary={}
        for p in m.pairs:
            v=values if p==pair else np.full(len(dates),10.)
            frame=pd.DataFrame(dict(date=dates,open=v,high=v*1.0001,low=v*.9999,close=v,volume=1e8))
            indexed=frame.set_index('date')
            primary[p]=indexed.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).reset_index()
            m.hourly[p]=indexed.resample('h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).reset_index().set_index('date',drop=False)
            m.prices[p]=indexed.open
            self.account.detail_data[p]=frame
        self.account.dataprovider.historic_ohlcv=lambda pair,timeframe,candle_type=None:m.hourly[pair].reset_index(drop=True).copy()
        self.account.backtest_one_strategy(self.account.strategy,primary,
            TimeRange.parse_timerange(f'{int(self.start.timestamp())}-{int(self.end.timestamp())}'))
        result=self.account.all_bt_content['F2CandleCost']['results']
        self.assertGreater(len(result),0)
        self.assertTrue(all(t.minute==5 and t.hour%4==0 for t in result.open_date))
        self.assertTrue(m.daily)
        self.assertTrue(any(o.ft_order_tag=='RD1_candle_reduction' and o.filled for t in LocalTrade.bt_trades for o in t.orders))
        self.assertIsNone(m.error)


if __name__=='__main__':unittest.main()
