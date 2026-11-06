"""Actual native DB/parser, captured local GET and complete fee reconciliation."""
import copy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'native_quantity_exit_20261106'))
import test_quantity_exit as qf
from exit_recovery import recover_exit_binding, columns, META
from native_exits import NativeRiskExit
from freqtrade.persistence import Trade, Order
from freqtrade.exceptions import TemporaryError
from rd1 import RiskError, StateError


class ExitRecoveryTests(unittest.TestCase):
    for _name in ('setUp','tearDown','provider','checkpoint','receipts','event','account','execute',
                  'receipt','boot','fill_cycle','harness','exit_harness','setup_exit','submit'):
        locals()[_name]=getattr(qf.QuantityExitTests,_name)
    del _name

    def unknown(self,market='spot',status='NEW',filled='0'):
        c,ex,wallet,p,guard,trade,bot,e,now,raw,action=self.setup_exit(market,lose_reply=True)
        with wallet.clock.at(now):
            with self.assertRaises(TemporaryError): self.submit()
        Trade.session.rollback()
        self.assertEqual(len(trade.orders),1)
        c.close();c=NativeRiskExit(self.root/'exits',self.entry_gate,wallet);self.opened.append(c);ex.controller=c
        now+=timedelta(seconds=1)
        raw=copy.deepcopy(raw)
        raw.update(status=status,executedQty=filled,cummulativeQuoteQty=str(D(filled)*5),
            cumQuote=str(D(filled)*5),avgPrice='5' if D(filled) else '0',updateTime=int(now.timestamp()*1000))
        ex._api.responses=[(raw,now)]
        aid=action['id']
        with wallet.clock.at(now): ex.query(aid,now)
        self.parts=(c,ex,wallet,p,guard,trade,bot,e,now,raw,action)
        return self.parts

    def test_spot_query_by_original_client_id_restores_order_without_post(self):
        c,ex,w,p,_,trade,_,_,now,_,action=self.unknown()
        before=columns(trade,META)
        with w.clock.at(now):
            bound=recover_exit_binding(c,action['id'],now)
            again=recover_exit_binding(c,action['id'],now)
            with self.assertRaises(RiskError): w.require_entry_clear(now)
        self.assertEqual(bound,again)
        self.assertEqual(columns(trade,META),before)
        self.assertEqual(len(trade.orders),2)
        self.assertEqual([r['method'] for r in ex._api.requests],['POST','GET'])
        self.assertEqual(ex._api.requests[-1]['params']['origClientOrderId'],c.s['actions'][action['id']]['client_order_id'])
        self.assertEqual(trade.orders[-1].ft_order_tag,c.s['actions'][action['id']]['client_order_id'])

    def reconcile(self,market,filled='5',status='FILLED'):
        c,ex,w,p,_,trade,bot,e,now,raw,action=self.unknown(market,status,filled)
        aid=action['id'];before=columns(trade,META)
        with w.clock.at(now): recover_exit_binding(c,aid,now)
        self.assertEqual(columns(trade,META),before)
        self.assertTrue(trade.is_open)
        quantity=D(filled)
        if quantity:
            p.append(self.event(trade,2,quantity=str(-quantity),fee=str(quantity*D('.005')),
                seconds=3,order_id='200',price='5'))
        remaining=5-quantity
        balance=(D('999.95')-quantity*5 if market=='futures' else D('949.95')+quantity*5)-quantity*D('.005')
        account=self.account(c.envelope(aid,now),trade if remaining else None,str(remaining),str(balance),
            cursor=self.inventory.snapshot.cursor+1,market=market)
        account=replace(account,snapshot=replace(account.snapshot,quotes=tuple(replace(q,price=D(5))
            if q.pair==trade.pair else q for q in account.snapshot.quotes)))
        with w.clock.at(now):
            bot.wallets=w.stage('recovered-exit',self.receipt(account,quantity=str(remaining),
                cash_used=str(remaining*10) if market=='futures' else '0',collateral=str(remaining*10),upnl=str(-remaining*5)),account,now)
            if quantity: c.apply_filled(aid,bot,now)
            else: c.apply_zero_terminal(aid,bot,now)
            checked,audit=self.checkpoint(p,account);w.confirm(checked,now)
            result=c.reconcile(aid,checked,now)
        self.assertEqual(result['state'],'TERMINAL_RECONCILED')
        self.assertEqual(trade.is_open,remaining>0)
        self.assertEqual(D(audit['fees_paid']),D('.05')+quantity*D('.005'))
        self.assertEqual(len(ex._api.requests),2)
        return c,ex,trade

    def test_spot_filled_query_recovers_then_applies_complete_fees(self): self.reconcile('spot')
    def test_futures_filled_query_recovers_then_applies_complete_fees(self): self.reconcile('futures')
    def test_partial_canceled_query_reconciles_remaining_inventory(self): self.reconcile('spot','2','CANCELED')
    def test_zero_canceled_query_keeps_true_terminal_order_and_native_cancel(self): self.reconcile('spot','0','CANCELED')

    def test_recovery_intent_write_failure_precedes_native_insert(self):
        c,ex,w,_,_,trade,_,_,now,_,action=self.unknown()
        with w.clock.at(now),patch.object(c.store,'save',side_effect=OSError('synthetic disk failure')):
            with self.assertRaisesRegex(StateError,'not acknowledged'): recover_exit_binding(c,action['id'],now)
        self.assertEqual(len(trade.orders),1)
        self.assertEqual(len(ex._api.requests),2)

    def test_native_commit_ack_loss_restart_does_not_duplicate_row(self):
        c,ex,w,_,_,trade,_,_,now,_,action=self.unknown()
        aid=action['id'];commit=Trade.commit
        def fail_after_commit(): commit();raise OSError('synthetic native commit acknowledgement loss')
        with w.clock.at(now),patch.object(Trade,'commit',side_effect=fail_after_commit):
            with self.assertRaisesRegex(OSError,'acknowledgement'): recover_exit_binding(c,aid,now)
        trade_id=trade.id;Trade.session.remove()
        c.close();c=NativeRiskExit(self.root/'exits',self.entry_gate,w);self.opened.append(c);ex.controller=c
        with w.clock.at(now): recover_exit_binding(c,aid,now)
        self.assertEqual(len(Trade.session.get(Trade,trade_id).orders),2)
        self.assertEqual(len(ex._api.requests),2)

    def test_new_query_advances_fill_after_uncertain_native_commit(self):
        c,ex,w,_,_,trade,_,_,now,raw,action=self.unknown()
        aid=action['id'];commit=Trade.commit
        def fail_after_commit(): commit();raise OSError('synthetic uncertain commit')
        with w.clock.at(now),patch.object(Trade,'commit',side_effect=fail_after_commit):
            with self.assertRaises(OSError): recover_exit_binding(c,aid,now)
        trade_id=trade.id;Trade.session.remove();trade=Trade.session.get(Trade,trade_id)
        now+=timedelta(seconds=1)
        raw.update(status='FILLED',executedQty='5',cummulativeQuoteQty='25',cumQuote='25',avgPrice='5',updateTime=int(now.timestamp()*1000))
        ex._api.responses=[(raw,now)]
        with w.clock.at(now):
            ex.query(aid,now);recover_exit_binding(c,aid,now)
        self.assertEqual(len(trade.orders),2)
        self.assertEqual(trade.orders[-1].filled,5)
        self.assertEqual(trade.amount,5)  # financial application is still pending
        self.assertTrue(trade.is_open)
        self.assertEqual(len(c.s['actions'][aid]['binding_recovery']['steps']),2)
        self.assertEqual([r['method'] for r in ex._api.requests],['POST','GET','GET'])

    def test_missing_query_is_not_reconstructed_from_request(self):
        c,ex,w,p,_,trade,_,_,now,_,action=self.setup_exit(lose_reply=True)
        with w.clock.at(now):
            with self.assertRaises(TemporaryError): self.submit()
        Trade.session.rollback()
        with w.clock.at(now):
            with self.assertRaisesRegex(RiskError,'Query the original'): recover_exit_binding(c,action['id'],now)
        self.assertEqual(len(trade.orders),1)

    def test_changed_campaign_finances_after_intent_are_rejected(self):
        c,ex,w,_,_,trade,_,_,now,_,action=self.unknown()
        aid=action['id']
        with w.clock.at(now),patch.object(Trade,'commit',side_effect=OSError('synthetic precommit failure')):
            with self.assertRaises(OSError): recover_exit_binding(c,aid,now)
        trade.stake_amount+=1;Trade.commit()
        with w.clock.at(now):
            with self.assertRaisesRegex(RiskError,'financial state changed'): recover_exit_binding(c,aid,now)
        self.assertEqual(len(trade.orders),1)

    def test_changed_recovered_order_fee_is_not_silently_erased(self):
        c,ex,w,_,_,trade,_,_,now,_,action=self.unknown()
        aid=action['id']
        with w.clock.at(now): recover_exit_binding(c,aid,now)
        trade.orders[-1].ft_fee_base=.01;Trade.commit()
        with w.clock.at(now):
            with self.assertRaisesRegex(RiskError,'both sides'): recover_exit_binding(c,aid,now)
        self.assertEqual(trade.orders[-1].ft_fee_base,.01)

    def test_binding_journal_ack_loss_reopens_and_binds_existing_row(self):
        c,ex,w,_,_,trade,_,_,now,_,action=self.unknown()
        aid=action['id'];save=c.store.save;calls=0
        def fail_on_binding(state):
            nonlocal calls
            calls+=1;save(state)
            if calls==2: raise OSError('synthetic binding acknowledgement loss')
        with w.clock.at(now),patch.object(c.store,'save',side_effect=fail_on_binding):
            with self.assertRaisesRegex(StateError,'not acknowledged'): recover_exit_binding(c,aid,now)
        c.close();c=NativeRiskExit(self.root/'exits',self.entry_gate,w);self.opened.append(c);ex.controller=c
        with w.clock.at(now): recover_exit_binding(c,aid,now)
        self.assertEqual(len(trade.orders),2)
        self.assertEqual(len(ex._api.requests),2)

    def test_wrong_client_response_cannot_create_order(self):
        c,ex,w,_,_,trade,_,_,now,_,action=self.unknown()
        c.s['actions'][action['id']]['response']['clientOrderId']='different-client'
        with w.clock.at(now):
            with self.assertRaises(RiskError): recover_exit_binding(c,action['id'],now)
        self.assertEqual(len(trade.orders),1)

    def test_source_order_owned_by_another_campaign_is_not_reassigned(self):
        c,ex,w,_,_,trade,_,_,now,_,action=self.unknown()
        other=Trade(**{a.key:getattr(trade,a.key) for a in trade.__mapper__.column_attrs if a.key!='id'})
        raw=c.s['actions'][action['id']]['response']
        order=Order.parse_from_ccxt_object(raw,trade.pair,'sell',5,5)
        order.ft_order_tag=c.s['actions'][action['id']]['client_order_id']
        other.orders.append(order);Trade.session.add(other);Trade.commit()
        with w.clock.at(now):
            with self.assertRaisesRegex(RiskError,'another native campaign'): recover_exit_binding(c,action['id'],now)
        self.assertEqual(order.ft_trade_id,other.id)
        self.assertEqual(len(trade.orders),1)

    def test_existing_native_zero_terminal_commit_is_recovered_and_cleaned(self):
        c,ex,w,p,_,trade,bot,e,now,_,action=self.setup_exit(status='CANCELED')
        aid=action['id'];commit=Trade.commit
        def fail_after_commit(): commit();raise OSError('synthetic submission commit acknowledgement loss')
        with w.clock.at(now),patch.object(Trade,'commit',side_effect=fail_after_commit):
            with self.assertRaises(OSError): self.submit()
        Trade.session.rollback()
        self.assertEqual(len(trade.orders),2)
        self.assertFalse(trade.orders[-1].ft_is_open)
        with w.clock.at(now): recover_exit_binding(c,aid,now)
        account=self.account(c.envelope(aid,now),trade,'5','949.95',cursor=5)
        account=replace(account,snapshot=replace(account.snapshot,quotes=tuple(replace(q,price=D(5))
            if q.pair==trade.pair else q for q in account.snapshot.quotes)))
        with w.clock.at(now):
            bot.wallets=w.stage('existing-zero-terminal',self.receipt(account,quantity='5'),account,now)
            c.apply_zero_terminal(aid,bot,now)
            checked,_=self.checkpoint(p,account);w.confirm(checked,now)
            result=c.reconcile(aid,checked,now)
        self.assertEqual(result['state'],'TERMINAL_RECONCILED')
        self.assertTrue(result['requires_further_exit'])
        self.assertEqual(trade.amount,5)
        self.assertEqual(len(ex._api.requests),1)


if __name__=='__main__': unittest.main()
