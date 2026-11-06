from test_reservations import snapshot,entry,pair,T
from lifecycle import Lifecycle
from rd1 import Order,Position,StateError,RiskError,stamp
from decimal import Decimal as D
from pathlib import Path
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch
import tempfile
import unittest


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'account'
        self.e=Lifecycle(self.path,'a','spot',create=True)
        self.e.reserve('r',snapshot(),entry(),T)

    def tearDown(self):self.e.close();self.tmp.cleanup()

    def claim(self):return self.e.claim_for_replay('r',snapshot(),T)

    def evidence(self,status='open',filled='2',remaining='3',cursor=2):
        client=self.e.s['requests']['r']['lifecycle']['client_order_id']
        report={'id':'exchange-1','clientOrderId':client,'symbol':pair('spot'),'side':'buy',
                'amount':'5','filled':filled,'remaining':remaining,'price':'10','status':status,'observed_at':stamp(T)}
        positions=() if D(filled)==0 else (Position('campaign',pair('spot'),D(filled),D(10),D(0)),)
        orders=() if status!='open' else (Order('exchange-1',pair('spot'),D(remaining),D(10),T,True),)
        snap=snapshot(cursor=cursor,balance=str(D(1000)-D(filled)*10),positions=positions,orders=orders)
        return report,snap

    def test_attempt_once_across_restart_unknown_retains_reservation(self):
        self.claim();self.e.close();self.e=Lifecycle(self.path,'a','spot')
        with self.assertRaises(StateError):self.claim()
        self.assertFalse(self.e.reserve('other',snapshot(),entry(),T)['reserved'])

    def test_stale_claim_not_consumed(self):
        with self.assertRaises(RiskError):self.e.claim_for_replay('r',snapshot(),T+timedelta(seconds=16))
        self.assertNotIn('lifecycle',self.e.s['requests']['r'])

    def test_partial_fill_transfers_only_to_exact_snapshot(self):
        self.claim();r,s=self.evidence()
        state=self.e.reconcile('r','event1',r,s,T)
        self.assertEqual(state['state'],'OBSERVED_OPEN')
        self.e._snapshot_digest=state['snapshot_hash']
        self.assertEqual(len(list(self.e.active_requests())),0)
        self.e._snapshot_digest=None
        self.assertEqual(len(list(self.e.active_requests())),1)

    def test_partial_expiry_requires_filled_position_before_release(self):
        self.claim();r,s=self.evidence('expired')
        with self.assertRaises(RiskError):self.e.reconcile('r','event1',r,replace(s,positions=()),T)
        state=self.e.reconcile('r','event1',r,replace(s,cursor=3),T)
        self.assertEqual(state['state'],'TERMINAL_RECONCILED')
        self.assertEqual(len(list(self.e.active_requests())),0)

    def test_zero_fill_rejection_release_and_restart(self):
        self.claim();r,s=self.evidence('rejected','0','5')
        self.e.reconcile('r','event1',r,s,T)
        self.e.close();self.e=Lifecycle(self.path,'a','spot')
        self.assertTrue(self.e.reserve('next',s,entry(),T)['reserved'])

    def test_missing_open_order_cannot_transfer_capacity(self):
        self.claim();r,s=self.evidence()
        with self.assertRaises(RiskError):self.e.reconcile('r','event1',r,replace(s,orders=()),T)

    def test_duplicate_event_idempotent_conflict_rejected(self):
        self.claim();r,s=self.evidence()
        first=self.e.reconcile('r','event1',r,s,T)
        self.assertEqual(first,self.e.reconcile('r','event1',r,s,T))
        with self.assertRaises(StateError):self.e.reconcile('r','event1',dict(r,filled='3'),s,T)

    def test_wrong_identity_and_quantity_rejected(self):
        self.claim();r,s=self.evidence()
        for bad in [dict(r,clientOrderId='wrong'),dict(r,symbol=pair('spot','ETH')),dict(r,amount='6'),dict(r,remaining='4')]:
            with self.subTest(bad=bad),self.assertRaises(RiskError):self.e.reconcile('r','bad',bad,s,T)

    def test_cumulative_fill_cannot_go_backwards(self):
        self.claim();r,s=self.evidence();self.e.reconcile('r','event1',r,s,T)
        r,s=self.evidence(filled='1',remaining='4',cursor=3)
        with self.assertRaises(RiskError):self.e.reconcile('r','event2',r,s,T)

    def test_terminal_snapshot_must_follow_claim(self):
        self.claim();r,s=self.evidence('canceled','0','5',cursor=1)
        with self.assertRaises(RiskError):self.e.reconcile('r','event1',r,s,T)

    def test_claim_persistence_failure_cannot_return_ticket(self):
        with patch.object(self.e.store,'save',side_effect=OSError('fault')):
            with self.assertRaises(StateError):self.claim()
        with self.assertRaises(StateError):self.claim()

    def test_cancel_race_includes_later_fills(self):
        self.claim();r,s=self.evidence();self.e.reconcile('r','partial',r,s,T)
        r,s=self.evidence('canceled','4','1',cursor=3)
        state=self.e.reconcile('r','cancel',r,s,T)
        self.assertEqual(state['filled'],'4')
        self.assertEqual(state['state'],'TERMINAL_RECONCILED')

    def test_full_fill_represented_as_position_not_released_cash(self):
        self.claim();r,s=self.evidence('closed','5','0')
        self.e.reconcile('r','fill',r,s,T)
        self.assertFalse(self.e.reserve('again',s,entry(),T)['reserved'])
        self.assertEqual(self.e.risk.equity(s),D(1000))


if __name__=='__main__':unittest.main()
