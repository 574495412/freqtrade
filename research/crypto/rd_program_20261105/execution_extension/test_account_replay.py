from test_reservations import snapshot,T
from account_replay import AccountReplay
from rd1 import RD1,Quote,Funding,RiskError
from pathlib import Path
from decimal import Decimal as D
from dataclasses import replace
import tempfile,unittest

AT='2026-11-06T12:00:00.0000000Z'

def event(n,kind='fill',**kw):
    e={'event_id':str(n),'sequence':n,'event_at':AT,'received_at':AT,'kind':kind}
    if kind=='fill':e.update(pair='BTC/USDT:USDT',signed_quantity='10',price='100',fee_quote='1',campaign_id='campaign1')
    else:e.update(funding_id='fund1',cost_quote='2')
    e.update(kw);return e

def quotes(market,price='90'):
    return {'BTC/USDT'+(':USDT' if market=='futures' else ''):{'price':price,'event_at':AT,'received_at':AT,'kind':'futures_mark' if market=='futures' else 'spot_bid'}}

class AccountTests(unittest.TestCase):
    def ledger(self,market='futures',basis='source_ordered'):return AccountReplay(market,sequence_basis=basis,evidence_class='synthetic')
    def value(self,l,price='90'):return l.value(quotes(l.market,price),AT,D('.001'),funding_complete=True)

    def test_futures_unrealized_loss_funding_settlement_not_double_counted(self):
        l=self.ledger();l.apply(event(1));self.assertEqual(self.value(l)['equity'],D('898.1'))
        l.apply(event(2,'funding_accrual'));self.assertEqual(self.value(l)['equity'],D('896.1'))
        l.apply(event(3,'funding_settlement'));self.assertEqual(self.value(l)['equity'],D('896.1'))
        l.apply(event(4,signed_quantity='-4',price='95',fee_quote='.38'))
        self.assertEqual(self.value(l)['equity'],D('916.08'))

    def test_short_credit_and_full_exit(self):
        l=self.ledger();l.apply(event(1,signed_quantity='-10'))
        l.apply(event(2,'funding_settlement',cost_quote='-2'))
        self.assertEqual(self.value(l)['equity'],D('1100.1'))
        l.apply(event(3,signed_quantity='10',price='90',fee_quote='.9'))
        self.assertEqual(self.value(l)['equity'],D('1100.1'));self.assertFalse(l.positions)

    def test_spot_cash_includes_reserved_money_only_once(self):
        l=self.ledger('spot');l.apply(event(1,pair='BTC/USDT',signed_quantity='5',price='10',fee_quote='.05'))
        self.assertEqual(self.value(l,'11')['equity'],D('1004.895'))

    def test_duplicate_conflict_and_gap_leave_state_unchanged(self):
        l=self.ledger();e=event(1);l.apply(e);self.assertFalse(l.apply(e))
        for bad in [event(1,price='101'),event(3)]:
            with self.assertRaises(RiskError):l.apply(bad)
            self.assertEqual(l.wallet,D(999));self.assertEqual(l.last_sequence,1)

    def test_unknown_same_time_funding_fill_is_rejected(self):
        l=self.ledger(basis='unknown');l.apply(event(1))
        with self.assertRaises(RiskError):l.apply(event(2,'funding_settlement'))
        self.assertEqual(l.wallet,D(999))

    def test_repeated_funding_id_under_new_event_id_is_rejected(self):
        l=self.ledger();l.apply(event(1,'funding_settlement'))
        with self.assertRaises(RiskError):l.apply(event(2,'funding_settlement'))
        self.assertEqual(l.wallet,D(998))

    def test_missing_quotes_and_future_event_rejected(self):
        l=self.ledger();l.apply(event(1))
        with self.assertRaises(RiskError):l.value({},AT,D('.001'),funding_complete=True)
        with self.assertRaises(RiskError):l.apply(event(2,event_at='2026-11-06T12:00:01Z'))
        with self.assertRaises(RiskError):l.value(quotes('futures'),AT,D('.001'),funding_complete=False)

    def test_account_snapshot_matches_frozen_rd1_equity(self):
        l=self.ledger();l.apply(event(1));l.apply(event(2,'funding_accrual'));value=self.value(l)
        with tempfile.TemporaryDirectory() as directory:
            risk=RD1(Path(directory),'replay','futures',create=True)
            try:
                s=snapshot('futures',balance=str(value['wallet']),positions=value['positions'],
                           quotes=(Quote('BTC/USDT:USDT',D(90),T,'futures_mark'),),funding=(Funding('fund1',D(2),False),))
                self.assertEqual(risk.equity(s),value['equity'])
                decision=risk.evaluate(s,T)
                self.assertFalse(decision.entry_allowed)
            finally:risk.close()

    def test_weighted_cost_basis_survives_partial_exit(self):
        l=self.ledger();l.apply(event(1,signed_quantity='2',price='10',fee_quote='0'))
        l.apply(event(2,signed_quantity='3',price='20',fee_quote='0'))
        l.apply(event(3,signed_quantity='-2',price='30',fee_quote='0'))
        self.assertEqual(l.positions['BTC/USDT:USDT']['entry_price'],D(16))
        self.assertEqual(self.value(l,'20')['equity'],D('1039.94'))

    def test_settlement_mismatch_does_not_erase_accrual(self):
        l=self.ledger();l.apply(event(1,'funding_accrual'))
        with self.assertRaises(RiskError):l.apply(event(2,'funding_settlement',cost_quote='3'))
        self.assertEqual(l.pending_funding,{'fund1':D(2)})
        self.assertEqual(l.wallet,D(1000));self.assertEqual(l.last_sequence,1)


if __name__=='__main__':unittest.main()
