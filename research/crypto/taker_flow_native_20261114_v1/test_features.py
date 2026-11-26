"""Actual source-kernel and native time/input boundary tests."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parent))
from FlowFeatures import FIELDS,calculate_features


class FlowTests(unittest.TestCase):
    def fixture(self):
        times=pd.date_range('2025-01-01',periods=70,tz='UTC',freq='h')
        hours=pd.DataFrame({'date':times})
        volume=np.arange(100.,170.);trades=np.linspace(10,30,70)
        flow=pd.DataFrame({'date':times,'base_volume':volume,'taker_buy_base_volume':volume*np.linspace(.3,.7,70),'number_of_trades':trades})
        return hours,flow

    def test_closed_bar_clock_and_complete_window(self):
        h,f=self.fixture();v=calculate_features(h,f)
        self.assertTrue(v[FIELDS].iloc[:19].isna().all().all())
        self.assertEqual(v.decision_date.iloc[19],h.date.iloc[19]+pd.Timedelta(hours=1))
        self.assertTrue(np.isfinite(v[FIELDS].iloc[19:]).all().all())

    def test_future_perturbation_and_prefix(self):
        h,f=self.fixture();v=calculate_features(h,f);cut=h.date.iloc[40]
        f.loc[f.date>=cut,'taker_buy_base_volume']*=.5
        changed=calculate_features(h,f)
        pd.testing.assert_frame_equal(v.loc[v.decision_date<=cut],changed.loc[changed.decision_date<=cut],check_exact=True)
        short=calculate_features(h.iloc[:40],f.iloc[:40])
        pd.testing.assert_frame_equal(v.iloc[:40],short,check_exact=True)

    def test_quantity_units_cancel(self):
        h,f=self.fixture();a=calculate_features(h,f)
        f[['base_volume','taker_buy_base_volume']]*=100
        b=calculate_features(h,f)
        np.testing.assert_allclose(a[FIELDS],b[FIELDS],rtol=0,atol=2e-12,equal_nan=True)

    def test_missing_hours_are_not_compressed(self):
        h,f=self.fixture()
        with self.assertRaisesRegex(ValueError,'compress'):calculate_features(h.drop(30),f)

    def test_absent_flow_is_unknown_not_zero(self):
        h,f=self.fixture();v=calculate_features(h,f.drop(30))
        self.assertTrue(v[FIELDS].iloc[30:50].isna().all().all())
        self.assertTrue(np.isfinite(v[FIELDS].iloc[50:]).all().all())

    def test_duplicate_and_impossible_buy_rejected(self):
        h,f=self.fixture()
        with self.assertRaisesRegex(ValueError,'Duplicate'):calculate_features(h,pd.concat([f,f.iloc[[0]]]))
        f.loc[40,'taker_buy_base_volume']=10000
        with self.assertRaisesRegex(ValueError,'exceeds'):calculate_features(h,f)

    def test_zero_activity_and_constant_size_unknown(self):
        h,f=self.fixture();f['number_of_trades']=f.base_volume/10
        v=calculate_features(h,f)
        self.assertTrue(v.avg_trade_size_z_20.isna().all())
        f[['base_volume','taker_buy_base_volume','number_of_trades']]=0
        self.assertTrue(calculate_features(h,f)[FIELDS].isna().all().all())


if __name__=='__main__':unittest.main()
