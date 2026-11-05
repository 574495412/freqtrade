"""Saved-fill accounting invariants; no financial experiment or strategy runs."""
import unittest
import pandas as pd

from audit_positions import reconstruct

T = pd.Timestamp('2026-01-01T00:00:00Z')
INDEX = pd.date_range(T,periods=5,freq='5min')
ZERO_COST = {'fee_bps_per_side':0,'execution_proxy_bps_per_side':0}


def order(minute, amount, price, entry):
    return {'order_filled_timestamp':int((T+pd.Timedelta(minutes=minute)).timestamp()*1000),
            'amount':amount,'safe_price':price,'ft_is_entry':entry}


def trade(orders, short=False, reason='force_exit', funding=0):
    return {'pair':'TEST','orders':orders,'is_short':short,'exit_reason':reason,
            'fee_open':0.,'fee_close':0.,'funding_fees':funding}


def run(trades,gross,equity):
    return reconstruct(trades,INDEX,{'TEST':pd.Series(10.,index=INDEX)},gross,equity,ZERO_COST)


class PositionAuditTests(unittest.TestCase):
    def test_partial_exit_dca_and_backdated_force_exit(self):
        result=run([trade([order(0,10,10,True),order(5,4,12,False),
                           order(10,2,9,True),order(15,8,8,False)])], [0,100,60,80,80],1010)
        self.assertTrue(result['model_terminal_attribution_reconciled'])
        self.assertEqual(result['DCA_entry_fills_before_reference_endpoint'],1)
        self.assertEqual(result['natural_completed_campaigns_before_reference_endpoint'],0)
        self.assertEqual(result['terminal_filled_open_positions'][0]['remaining_quantity'],8)
        self.assertEqual(result['filled_orders_before_reference_endpoint'],3)
        self.assertEqual(result['hypothetical_terminal_exit_cost_reserve_usdt'],0)

    def test_endpoint_fills_are_after_snapshot(self):
        result=run([trade([order(20,10,10,True),order(20,10,10,False)])],[0]*5,1000)
        self.assertEqual(result['terminal_filled_open_position_count'],0)
        self.assertEqual(result['filled_orders_before_reference_endpoint'],0)

    def test_short_direction_and_funding_sign(self):
        result=run([trade([order(0,10,12,True),order(10,10,10,False)],
                           short=True,reason='signal',funding=-3)], [0,100,100,0,0],1017)
        self.assertTrue(result['model_terminal_attribution_reconciled'])
        self.assertEqual(result['direction_exposure']['net_notional_min'],-100)
        self.assertEqual(result['native_terminal_funding_total_usdt'],-3)
        self.assertEqual(result['natural_completed_campaigns_before_reference_endpoint'],1)

    def test_discrepancy_refuses_attribution_and_gross_error_raises(self):
        record=trade([order(0,10,10,True),order(15,10,10,False)],reason='signal')
        result=run([record],[0,100,100,100,0],1001)
        self.assertFalse(result['model_terminal_attribution_reconciled'])
        self.assertIsNone(result['max_positive_pair_contribution_share'])
        with self.assertRaises(ValueError):run([record],[0]*5,1000)

    def test_natural_exit_at_endpoint_keeps_position_and_unknown_final_funding(self):
        record=trade([order(0,10,10,True),order(20,10,9,False)],reason='signal',funding=-1)
        result=run([record],[0,100,100,100,100],999)
        self.assertEqual(result['terminal_filled_open_position_count'],1)
        self.assertFalse(result['model_terminal_attribution_reconciled'])
        self.assertEqual(result['natural_completed_campaigns_before_reference_endpoint'],0)


if __name__=='__main__':unittest.main(verbosity=2)
