"""Meaningful missing-input, timing and exact-source tie boundaries."""
import unittest

import numpy as np
import pandas as pd

from research.crypto.funding_cross_section_native_20261114_v1.FundingFeatures import observed_daily_funding,source_inputs


class FeatureTests(unittest.TestCase):
    def funding(self):
        days=pd.date_range('2023-01-01',periods=25,tz='UTC',freq='D')
        return pd.DataFrame({f'P{i}':np.full(len(days),(i+1)*.0001) for i in range(6)},index=days)

    def test_source_complete_tie_short_overwrites_long(self):
        f=self.funding();f.loc[:,:]=.0003
        result=source_inputs(f)
        row=result['weights'].loc['2023-01-09']
        self.assertTrue(np.array_equal(row.to_numpy(),np.full(6,-.5/6)))
        self.assertAlmostEqual(row.sum(),-.5)
        self.assertEqual(result['decisions'][0]['overlap_members'],list(f.columns))

    def test_exact_five_day_anchor_and_no_warmup_zero_factor(self):
        result=source_inputs(self.funding())
        self.assertTrue(result['factor'].iloc[:7].isna().all().all())
        self.assertEqual([r['date'][:10] for r in result['decisions']],['2023-01-09','2023-01-14','2023-01-19','2023-01-24'])
        self.assertTrue(result['weights'].loc[:'2023-01-08'].eq(0).all().all())

    def test_unknown_funding_day_propagates_seven_decisions(self):
        f=self.funding();f.loc['2023-01-10','P0']=np.nan
        score=source_inputs(f)['factor']['P0']
        self.assertTrue(score.loc['2023-01-11':'2023-01-17'].isna().all())
        self.assertTrue(np.isfinite(score.loc['2023-01-10']))
        self.assertTrue(np.isfinite(score.loc['2023-01-18']))

    def test_future_perturbation_invariant(self):
        f=self.funding();baseline=source_inputs(f);f.loc['2023-01-16':]*=100
        changed=source_inputs(f)
        for key in ['factor','weights']:
            pd.testing.assert_frame_equal(baseline[key].loc[:'2023-01-16'],changed[key].loc[:'2023-01-16'],check_exact=True)

    def test_missing_calendar_not_compressed(self):
        with self.assertRaisesRegex(ValueError,'compress'):
            source_inputs(self.funding().drop(pd.Timestamp('2023-01-10',tz='UTC')))

    def event_frame(self):
        times=pd.date_range('2023-01-01',periods=6,tz='UTC',freq='4h')
        return pd.DataFrame({'pair':['TIA']*6,'event_time':times,'date':times,'funding_interval_hours':[4]*6,'last_funding_rate':[.0001]*6})

    def test_actual_four_hour_sum_and_declared_gap_unknown(self):
        f=self.event_frame()
        daily=observed_daily_funding(f,['TIA'],'2023-01-01T00:00Z','2023-01-02T00:00Z')
        self.assertAlmostEqual(daily.iloc[0,0],.0006)
        self.assertTrue(pd.isna(daily.iloc[1,0]))
        masked=observed_daily_funding(f.iloc[[0,1,3,4,5]],['TIA'],'2023-01-01T00:00Z','2023-01-02T00:00Z',[('TIA',pd.Timestamp('2023-01-01',tz='UTC'))])
        self.assertTrue(pd.isna(masked.iloc[0,0]))

    def test_duplicate_and_late_availability(self):
        f=self.event_frame()
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            observed_daily_funding(pd.concat([f,f.iloc[[0]]]),['TIA'],'2023-01-01T00:00Z','2023-01-01T00:00Z')
        f.loc[5,'event_time']=pd.Timestamp('2023-01-01T23:50Z')
        daily=observed_daily_funding(f,['TIA'],'2023-01-01T00:00Z','2023-01-01T00:00Z')
        self.assertTrue(pd.isna(daily.iloc[0,0]))

    def test_future_gap_does_not_extend_prefix(self):
        f=self.event_frame()
        daily=observed_daily_funding(f,['TIA'],'2023-01-01T00:00Z','2023-01-01T00:00Z',[('TIA',pd.Timestamp('2026-06-24',tz='UTC'))])
        self.assertEqual(len(daily),1)


if __name__=='__main__':unittest.main()
