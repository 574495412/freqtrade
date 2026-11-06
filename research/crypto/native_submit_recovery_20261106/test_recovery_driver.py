"""The registered driver recovers a lost native exit before its full batch."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
import sys
import unittest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'native_quantity_exit_20261106'))
import test_quantity_driver as qd
from exit_recovery import recover_exit_binding, query_and_recover_exit
from freqtrade.exceptions import TemporaryError
from freqtrade.persistence import Trade
from rd1 import StateError
import ccxt


class RecoveryDriverTests(unittest.TestCase):
    for _name in ('setUp','tearDown','provider','checkpoint','receipts','bars','setup_runtime','account',
                  'prepare_response','reports','fill_events','fixture','make','wallet_receipt','financial',
                  'dispatch','observe','advance','enter_two','exit_exchange','setup_exit'):
        locals()[_name]=getattr(qd.QuantityDriverTests,_name)
    del _name

    def test_registered_lost_exit_recovers_and_completes_full_account_batch(self):
        c,now,first,second,e,inventory,ex,raw,aid,bid=self.setup_exit()
        ex._api.responses=[ccxt.RequestTimeout('synthetic lost exit response')]
        with c.wallet.clock.at(now):
            with self.assertRaises(TemporaryError):
                c.driver.quantity_exit(first,inventory,ex,c.bot,now,exit_rate=bid,
                    complete_sequence=c.provider.ledger.last_sequence)
        Trade.session.rollback()
        now+=timedelta(seconds=1)
        raw.update(status='FILLED',executedQty=raw['origQty'],cummulativeQuoteQty='48.4',
            cumQuote='48.4',avgPrice='5',updateTime=int(now.timestamp()*1000))
        ex._api.responses=[(raw,now)]
        with c.wallet.clock.at(now):
            with self.assertRaisesRegex(StateError,'driver must own'): recover_exit_binding(c.exits,aid,now)
            query_and_recover_exit(c.driver,ex,aid,c.bot,now)
        self.assertTrue(first.is_open)
        self.assertEqual(first.amount,9.68)
        c.trade=first
        event=self.financial(c,3,now,'fill',order_id='200',fill_id='recovered-driver-fill-200',
            signed_quantity='-9.68',price='5',fee_quote='.0484',fee_base='0')
        remaining=tuple(p for p in e.snapshot.positions if p.pair==second.pair)
        final=replace(e,snapshot=replace(e.snapshot,cursor=inventory.snapshot.cursor+1,
            at=now,balance_at=now,orders_at=now,balance=D('951.358'),positions=remaining,
            quotes=tuple(replace(q,at=now) for q in e.snapshot.quotes)),
            position_bindings={p.position_id:e.position_bindings[p.position_id] for p in remaining})
        with c.wallet.clock.at(now): result=self.observe(c,'recovered-quantity-terminal',final,[event])
        self.assertFalse(first.is_open)
        self.assertTrue(second.is_open)
        self.assertEqual(c.exits.s['actions'][aid]['state'],'TERMINAL_RECONCILED')
        self.assertEqual(D(result['audit']['observed_wallet']),D('951.358'))
        self.assertEqual(D(result['audit']['fees_paid']),D('.242'))
        self.assertEqual([r['method'] for r in ex._api.requests],['POST','GET'])


if __name__=='__main__': unittest.main()
