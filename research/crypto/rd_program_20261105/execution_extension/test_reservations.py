import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'p0/risk'))
from test_rd1 import snapshot, entry, pair, T
from rd1 import StateError
from reservations import Reservations
from dataclasses import replace
from decimal import Decimal as D
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import tempfile
import unittest


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.e=Reservations(Path(self.tmp.name)/'account','test','spot',create=True)

    def tearDown(self):
        self.e.close()
        self.tmp.cleanup()

    def test_same_pair_concurrent_requests_only_one_reserves(self):
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda i:self.e.reserve(str(i),snapshot(),entry(),T),range(2)))
        self.assertEqual(sum(r['reserved'] for r in results),1)

    def test_exact_retry_and_conflicting_identity(self):
        a=self.e.reserve('a',snapshot(),entry(),T)
        b=self.e.reserve('a',snapshot(),entry(),T)
        self.assertTrue(a['reserved']);self.assertTrue(b['duplicate'])
        self.assertEqual(a['receipt'],b['receipt'])
        with self.assertRaises(StateError):self.e.reserve('a',snapshot(),entry(quantity=D(4)),T)
        self.assertFalse(a['submission_authorized'])

    def test_cluster_limit_counts_requests_absent_from_snapshot(self):
        # Missing correlation matrix conservatively puts all assets in one cluster.
        results=[]
        for i,asset in enumerate(['BTC','ETH','SOL','AVAX']):
            results.append(self.e.reserve(str(i),snapshot(),entry(pair=pair('spot',asset),quantity=D(9)),T))
        self.assertTrue(all(r['reserved'] for r in results[:3]))
        self.assertFalse(results[3]['reserved'])
        self.assertIn('cluster_exposure',results[3]['reasons'])

    def test_restart_keeps_reservation_and_rejects_another_writer(self):
        self.e.reserve('a',snapshot(),entry(),T)
        with self.assertRaises(StateError):Reservations(Path(self.tmp.name)/'account','test','spot')
        self.e.close()
        self.e=Reservations(Path(self.tmp.name)/'account','test','spot')
        self.assertFalse(self.e.reserve('b',snapshot(),entry(),T)['reserved'])

    def test_failed_commit_returns_no_receipt_and_poisoned_instance(self):
        with patch.object(self.e.store,'save',side_effect=OSError('disk fault')):
            with self.assertRaises(StateError):self.e.reserve('a',snapshot(),entry(),T)
        with self.assertRaises(StateError):self.e.reserve('b',snapshot(),entry(),T)

    def test_missing_quote_for_old_reservation_blocks_new_entry(self):
        self.e.reserve('a',snapshot(),entry(),T)
        s=snapshot(cursor=2)
        s=replace(s,quotes=tuple(q for q in s.quotes if q.pair!=pair('spot')))
        r=self.e.reserve('b',s,entry(pair=pair('spot','ETH')),T)
        self.assertFalse(r['reserved'])

    def test_closed_writer_cannot_return_permission(self):
        self.e.close()
        with self.assertRaises(StateError):self.e.reserve('a',snapshot(),entry(),T)

    def test_journal_survives_interruption_before_state_replace(self):
        original=self.e.store.atomic_write
        def fail_state(path,payload):
            if path.name=='state.json':raise OSError('crash after journal fsync')
            return original(path,payload)
        with patch.object(self.e.store,'atomic_write',side_effect=fail_state):
            with self.assertRaises(StateError):self.e.reserve('a',snapshot(),entry(),T)
        self.e.close()
        self.e=Reservations(Path(self.tmp.name)/'account','test','spot')
        self.assertTrue(self.e.reserve('a',snapshot(),entry(),T)['duplicate'])
        self.assertFalse(self.e.reserve('b',snapshot(),entry(),T)['reserved'])

    def test_futures_short_reserves_gross_even_when_net_offsets(self):
        e=Reservations(Path(self.tmp.name)/'futures','future','futures',create=True)
        try:
            for i,asset in enumerate(['BTC','ETH','SOL','AVAX']):
                r=e.reserve(str(i),snapshot('futures'),entry('futures',pair=pair('futures',asset),quantity=D(9 if i%2 else -9)),T)
                self.assertEqual(r['reserved'],i<3)
            self.assertIn('cluster_exposure',r['reasons'])
        finally:e.close()

    def test_returned_receipt_cannot_mutate_internal_capacity(self):
        r=self.e.reserve('a',snapshot(),entry(),T)
        r['receipt']['proposal']['quantity']='0'
        self.assertEqual(self.e.s['requests']['a']['proposal']['quantity'],'5')


if __name__=='__main__':unittest.main()
