"""Synthetic causal invariants; these tests do not measure financial returns."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import math
import unittest

from features import HourClose, Feature, momentum168, volatility24_scale, select_positive_top3

T = datetime(2026, 1, 10, tzinfo=timezone.utc)
H = timedelta(hours=1)


def bars(count=169):
    return [HourClose(T - (count-i)*H, 100 * math.exp(i*0.001), T-(count-i-1)*H)
            for i in range(count)]


class FeatureCausalityTests(unittest.TestCase):
    def test_momentum_uses_168_intervals_and_169_completed_closes(self):
        value = momentum168(bars(), T)
        self.assertAlmostEqual(value.value, .168)
        self.assertEqual(value.observations, 169)
        self.assertIsNone(value.blocked_reason)
        self.assertEqual(value.proposed_execution_at, T + timedelta(minutes=5))

    def test_future_values_and_unclosed_current_bar_do_not_change_features(self):
        history = bars()
        # Changed future prices, missing future receipt and duplicate future rows
        # must not alter the exact causal prefix.
        future = [HourClose(T + n*H, float('nan') if n%2 else 1e200, None)
                  for n in range(100)]
        for function in (momentum168, volatility24_scale):
            self.assertEqual(function(history, T), function(history+future+future, T))
            self.assertEqual(function(history, T), function(list(reversed(history)), T))

    def test_latest_close_is_unavailable_until_its_observed_receipt(self):
        history = bars()
        history[-1] = replace(history[-1], available_at=T+timedelta(microseconds=1))
        for function in (momentum168, volatility24_scale):
            self.assertEqual(function(history, T).blocked_reason, 'not_available_at_decision')

    def test_missing_duplicate_unknown_early_receipt_and_invalid_price_block(self):
        for change, reason in [
            (lambda b:b[:-1], 'missing_hour'),
            (lambda b:b+[b[-1]], 'duplicate_hour'),
            (lambda b:b[:-1]+[replace(b[-1],available_at=None)], 'availability_unknown'),
            (lambda b:b[:-1]+[replace(b[-1],available_at=T-timedelta(seconds=1))], 'receipt_before_hour_complete'),
            (lambda b:b[:-1]+[replace(b[-1],close=float('inf'))], 'invalid_close'),
            (lambda b:b[:-1]+[replace(b[-1],close=0)], 'invalid_close'),
        ]:
            with self.subTest(reason=reason):
                for function in (momentum168, volatility24_scale):
                    result = function(change(bars()), T)
                    self.assertIsNone(result.value)
                    self.assertEqual(result.blocked_reason, reason)

    def test_24_returns_need_25_closes_and_use_sample_variance(self):
        increments = [0.01, -0.01] * 12
        price = 100.
        history = [HourClose(T-25*H,price,T-24*H)]
        for i, value in enumerate(increments):
            price *= math.exp(value)
            history.append(HourClose(T-(24-i)*H,price,T-(23-i)*H))
        sigma = math.sqrt(24*.01**2/23)*math.sqrt(24*365)
        result = volatility24_scale(history,T)
        self.assertAlmostEqual(result.value,.2/sigma)
        self.assertEqual(result.observations,25)
        self.assertEqual(volatility24_scale(history[1:],T).blocked_reason,'missing_hour')

    def test_zero_volatility_blocks_and_low_volatility_never_increases_exposure(self):
        flat = [replace(row,close=100.) for row in bars(25)]
        self.assertEqual(volatility24_scale(flat,T).blocked_reason,'zero_volatility')
        low = [replace(row,close=100.+(i%2)*1e-5) for i,row in enumerate(flat)]
        self.assertEqual(volatility24_scale(low,T).value,1.)

    def test_decisions_are_utc_midnight_not_naive_or_intraday(self):
        for bad in (T.replace(tzinfo=None),T+H,T+timedelta(microseconds=1)):
            with self.assertRaises(ValueError): momentum168(bars(),bad)
        local = T.astimezone(timezone(timedelta(hours=8)))
        self.assertEqual(momentum168(bars(),local),momentum168(bars(),T))

    def test_fixed_universe_ties_positive_filter_and_missing_member(self):
        universe = ('B','A','C','D','E')
        def f(v): return Feature(v,None,T,T+timedelta(minutes=5),169)
        values = {'E':f(-1),'D':f(.1),'C':f(.2),'A':f(.3),'B':f(.3)}
        result = select_positive_top3(values,universe,T)
        self.assertEqual(result['selected'],['B','A','C'])
        self.assertFalse(result['orders_enabled'])
        values.pop('E')
        result = select_positive_top3(values,universe,T)
        self.assertEqual(result['selected'],[])
        self.assertEqual(result['blocked_members'],{'E':'missing_member'})

    def test_mixed_decision_times_and_failure_cannot_enter_ranking(self):
        value = momentum168(bars(),T)
        for changed, reason in [(replace(value,decision_at=T-H),'different_decision_time'),
                                (replace(value,blocked_reason='availability_unknown',value=None),'availability_unknown')]:
            result=select_positive_top3({'BTC':changed},('BTC',),T)
            self.assertEqual(result['selected'],[])
            self.assertEqual(result['blocked_members']['BTC'],reason)


if __name__ == '__main__':
    unittest.main(verbosity=2)
