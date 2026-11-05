"""Accounting boundary tests, independent of historical performance."""
import unittest

import numpy as np
import pandas as pd

from revalue_frozen_positions import inventory, valuation_delta


class MarkValuationTests(unittest.TestCase):
    def setUp(self):
        self.index=pd.date_range('2026-01-01T00:00:00Z',periods=6,freq='5min')

    def order(self, minute, amount, entry):
        at=self.index[0]+pd.Timedelta(minutes=minute)
        return {'order_filled_timestamp':int(at.timestamp()*1000),'amount':amount,'ft_is_entry':entry}

    def test_partial_dca_and_same_timestamp_are_pre_event(self):
        trade={'exit_reason':'roi','orders':[self.order(0,2,True),self.order(5,1,False),self.order(10,3,True),self.order(20,4,False)]}
        np.testing.assert_array_equal(inventory(trade,self.index),[0,2,1,4,4,0])

    def test_force_close_does_not_remove_open_inventory_even_if_backdated(self):
        trade={'exit_reason':'force_exit','orders':[self.order(0,2,True),self.order(10,2,False)]}
        np.testing.assert_array_equal(inventory(trade,self.index),[0,2,2,2,2,2])

    def test_natural_endpoint_exit_occurs_after_last_snapshot(self):
        trade={'exit_reason':'roi','orders':[self.order(0,2,True),self.order(25,2,False)]}
        self.assertEqual(inventory(trade,self.index)[-1],2)

    def test_long_and_short_fee_reserve_have_same_cost_sign(self):
        # 2 units, price rises100->110; P&L +/-20, reserve rises0.02.
        self.assertAlmostEqual(valuation_delta([2],[100],[110],False,.001)[0],19.98)
        self.assertAlmostEqual(valuation_delta([2],[100],[110],True,.001)[0],-20.02)
        self.assertEqual(valuation_delta([0],[100],[110],False,.001)[0],0)
        self.assertEqual(valuation_delta([2],[100],[100],True,.001)[0],0)

    def test_overexit_and_missing_prices_rejected(self):
        trade={'exit_reason':'roi','orders':[self.order(0,2,True),self.order(5,3,False)]}
        with self.assertRaises(ValueError): inventory(trade,self.index)
        with self.assertRaises(ValueError): valuation_delta([2],[100],[np.nan],False,.001)


if __name__=='__main__': unittest.main()
