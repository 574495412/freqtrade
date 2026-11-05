"""Independent boundary/account identities for offline accounting references."""
import unittest
from decimal import Decimal
import pandas as pd
import numpy as np
from account_controls import account_curve, metrics

class AccountControlsTests(unittest.TestCase):
    def setUp(self):
        self.index=pd.date_range('2025-01-01',periods=16,freq='5min',tz='UTC')
        self.prices=pd.Series(100.,index=self.index)

    def test_spot_flat_market_loses_exact_two_costs_with_cash_reserve(self):
        curve,meta=account_curve(self.index,self.prices,.0013,.001)
        self.assertEqual(meta['quantity'],9.887)
        self.assertEqual(curve.reference_equity.iloc[0],1000)
        self.assertEqual(curve.reference_equity.iloc[1],1000) # before entry at00:05
        expected=Decimal('1000')-Decimal('9.887')*Decimal('100')*Decimal('.0013')*2
        self.assertAlmostEqual(curve.reference_equity.iloc[-1],float(expected),10)
        self.assertLessEqual(meta['entry_notional']+meta['entry_fee'],990)
        self.assertEqual(meta['natural_complete_campaigns'],0)

    def test_futures_margin_not_double_counted_and_funding_occurs_after_boundary(self):
        times=pd.DatetimeIndex([self.index[0],self.index[12]])
        marks=pd.Series([100.,120.],index=times)
        funding=pd.Series([.5,.01],index=times) # initial event precedes opening, excluded
        curve,meta=account_curve(self.index,self.prices,.0008,.001,'futures',funding,marks)
        qty=meta['quantity'];base=1000-qty*100*.0008*2
        self.assertAlmostEqual(curve.reference_equity.iloc[12],base,10)
        self.assertAlmostEqual(curve.reference_equity.iloc[13],base-qty*120*.01,10)
        self.assertEqual(len(meta['funding_events']),1)
        self.assertAlmostEqual(meta['hourly_mark_end_equity'],1000-qty*100*.0008+qty*20-qty*120*.0008,10)
        self.assertTrue(np.isnan(curve.hourly_mark_equity.iloc[13])) # no fabricated5m mark

    def test_missing_price_or_funding_mark_is_rejected(self):
        with self.assertRaises(ValueError):account_curve(self.index,self.prices.drop(self.index[5]),.001,.001)
        funding=pd.Series([.01],index=pd.DatetimeIndex([self.index[6]]))
        marks=pd.Series([100.,100.],index=pd.DatetimeIndex([self.index[0],self.index[12]]))
        with self.assertRaises(ValueError):account_curve(self.index,self.prices,.001,.001,'futures',funding,marks)

    def test_cash_zero_variance_has_no_infinite_ratios(self):
        index=pd.date_range('2025-01-01',periods=3*288,freq='5min',tz='UTC')
        result=metrics(pd.DataFrame({'date':index,'reference_equity':1000.}))
        self.assertEqual(result['net_reference_return'],0)
        self.assertEqual(result['reference_mdd'],0)
        self.assertIsNone(result['daily_NAV_Sharpe'])
        self.assertIsNone(result['daily_Sortino'])

if __name__=='__main__':unittest.main()
