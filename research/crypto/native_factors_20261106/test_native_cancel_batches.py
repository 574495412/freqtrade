"""Cancellation receipts share the complete source-account batch with other orders."""
import copy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'sources'))
import test_native_risk_runtime as rr
import test_native_factor_runtime as mf
import test_native_entries as ef
from native_factor_runtime import NativeFactorRuntime
from native_cancellations import NativeCancellation,LocalCancelExchange
from native_entries import NativeEntryGate,LocalBinanceTape
from native_evidence import CapturedOrder
from freqtrade.persistence import Trade
from rd1 import RiskError,StateError,Funding


class CancellationBatchTests(unittest.TestCase):
    for _name in ('setUp','tearDown','provider','checkpoint','receipts','event','account','execute','receipt','boot',
                  'harness','runtime','observed','cancel_tape','terminal_account'):
        locals()[_name]=getattr(rr.NativeRiskRuntimeTests,_name)
    del _name

    def batch(self,r,bot,label,e,receipt,reports,events=(),*,include_controller=True,cancel_requests=('r1',)):
        return NativeFactorRuntime(r.gate,r.wallet).observe_account(label,e,receipt,self.receipts(e),reports,events,bot,e.snapshot.at,
            complete_sequence=max([r.provider.ledger.last_sequence]+[event['sequence'] for event in events]),
            complete_from_inception=True,funding_complete=True,
            cancel_controller=r.cancellations if include_controller else None,cancel_requests=cancel_requests if include_controller else ())

    def pending_cancel(self,market='spot',status='CANCELED'):
        r,trade,bot,result,_,_,_,now=self.observed(market)
        received=now+timedelta(seconds=1);ex=self.cancel_tape(r,received,status=status)
        with r.wallet.clock.at(now):report=r.cancel('r1',result['evidence'],ex,now)
        e,receipt=self.terminal_account(r,trade,report)
        event=self.event(trade,2,quantity='1',fee='.01',seconds=2)
        return r,trade,bot,ex,report,e,receipt,event

    def cycle(self,market,fault=False):
        r,trade,bot,ex,report,e,receipt,event=self.pending_cancel(market)
        with r.wallet.clock.at(e.snapshot.at):
            if fault:
                with patch.object(r.provider,'checkpoint',side_effect=OSError('synthetic cancel batch account interruption')):
                    with self.assertRaises(OSError):self.batch(r,bot,'cancel',e,receipt,{'r1':report},[event])
                with self.assertRaises(RiskError):r.gate.require_account_batch_clear()
                with self.assertRaises(RiskError):r.cancellations.query('r1',ex,e.snapshot.at)
                r.cancellations.close();r.gate.close()
                gate=NativeEntryGate(self.root/(market+'-receipts'),r.gate.lifecycle,after_fill_guard=r.guard);self.opened.append(gate)
                r.wallet.attach(gate);r.gate=gate
                r.cancellations=NativeCancellation(self.root/'cancel-runtime',gate);self.opened.append(r.cancellations)
                with patch.object(bot.strategy,'order_filled',side_effect=AssertionError('cancel callback repeated')):
                    result=self.batch(r,bot,'cancel',e,receipt,{'r1':report},[event])
            else:result=self.batch(r,bot,'cancel',e,receipt,{'r1':report},[event])
            r.guard.require_entry_clear();r.gate.require_account_batch_clear()
        self.assertEqual(trade.amount,4);self.assertEqual(D(result['audit']['fees_paid']),D('.04'))
        self.assertEqual(D(result['decision'].equity),D('971.9444') if market=='spot' else D('971.9504'))
        self.assertEqual(r.cancellations.s['requests']['r1']['state'],'TERMINAL_RECONCILED')
        self.assertEqual([x['method'] for x in ex._api.requests],['DELETE','GET'])

    def test_spot_cancel_race_fee_and_remaining_position_share_batch(self):self.cycle('spot')
    def test_futures_cancel_race_fee_collateral_and_floating_loss_share_batch(self):self.cycle('futures')
    def test_cancel_batch_account_failure_reopens_without_callbacks_or_second_delete(self):self.cycle('spot',fault=True)

    def test_futures_cancel_race_and_funding_share_cash_collateral_and_nav(self):
        r,trade,bot,ex,report,e,receipt,event=self.pending_cancel('futures')
        funding=self.event(trade,3,seconds=2,kind='funding_settlement',funding_id='cancel-funding',cost_quote='2')
        e=replace(e,snapshot=replace(e.snapshot,balance=D('997.96'),funding=(Funding('cancel-funding',D(2),True),)))
        receipt=self.receipt(e,quantity='4',cash_used='40',collateral='40',upnl='-28')
        with r.wallet.clock.at(e.snapshot.at):
            result=self.batch(r,bot,'cancel-and-fund',e,receipt,{'r1':report},[event,funding])
        self.assertEqual(trade.amount,4);self.assertEqual(trade.funding_fees,-2)
        self.assertEqual(trade.funding_fee_running,-2);self.assertEqual(trade.orders[0].funding_fee,0)
        self.assertEqual(D(result['decision'].equity),D('969.9504'))
        self.assertEqual(D(result['audit']['fees_paid']),D('.04'))
        self.assertEqual(D(result['audit']['settled_funding_cash']),D(-2))

    def test_open_cancel_result_accepts_race_fill_then_query_terminal_without_new_fill(self):
        r,trade,bot,ex,report,e,receipt,event=self.pending_cancel(status='PARTIALLY_FILLED')
        # Existing helper describes terminal wallet usage; open remaining one unit still reserves ten.
        receipt=self.receipt(e,quantity='4',cash_used='10')
        with r.wallet.clock.at(e.snapshot.at):
            self.batch(r,bot,'cancel-open',e,receipt,{'r1':report},[event])
            self.assertEqual(trade.amount,0)
            with self.assertRaises(RiskError):r.guard.require_entry_clear()
        now=e.snapshot.at+timedelta(seconds=1)
        raw=copy.deepcopy(ex.records[-1]['raw']);raw.update(status='CANCELED',updateTime=int(now.timestamp()*1000))
        ex._api.responses=[(raw,now)]
        with r.wallet.clock.at(now):
            report=r.cancellations.query('r1',ex,now)
            e,receipt=self.terminal_account(r,trade,report);e=replace(e,snapshot=replace(e.snapshot,cursor=4))
            result=self.batch(r,bot,'cancel-terminal',e,receipt,{'r1':report})
            r.guard.require_entry_clear()
        self.assertEqual(trade.amount,4);self.assertEqual(D(result['audit']['fees_paid']),D('.04'))
        self.assertEqual([x['method'] for x in ex._api.requests],['DELETE','GET','GET'])

    def test_missing_registered_controller_or_cancel_inventory_revokes_wallet(self):
        r,trade,bot,ex,report,e,receipt,event=self.pending_cancel()
        with r.wallet.clock.at(e.snapshot.at):
            with self.assertRaisesRegex(RiskError,'Registered cancellation controller'):
                self.batch(r,bot,'missing-controller',e,receipt,{'r1':report},[event],include_controller=False)
            with self.assertRaises(RiskError):r.wallet.require_entry_clear(e.snapshot.at)
            with self.assertRaisesRegex(RiskError,'every unresolved cancellation'):
                self.batch(r,bot,'missing-cancel',e,receipt,{'r1':report},[event],cancel_requests=())
        self.assertEqual(r.provider.ledger.last_sequence,1);self.assertEqual(trade.amount,0)

    def test_forged_cancel_receipt_or_missing_race_fill_rejects_before_release(self):
        r,trade,bot,ex,report,e,receipt,event=self.pending_cancel()
        with r.wallet.clock.at(e.snapshot.at):
            with self.assertRaisesRegex(RiskError,'exact controller-derived'):
                self.batch(r,bot,'forged',e,receipt,{'r1':replace(report,source_id='caller-assertion')},[event])
            with self.assertRaisesRegex(RiskError,'misses cumulative'):
                self.batch(r,bot,'missing-fill',e,receipt,{'r1':report})
            with self.assertRaises(RiskError):r.gate.require_account_batch_clear()
        self.assertEqual(trade.amount,0)

    def test_zero_cancel_deletes_native_entry_and_resumes_account_failure(self):
        r,trade,bot=self.runtime();now=ef.T+timedelta(seconds=61)
        raw=copy.deepcopy(r.gate.s['requests']['r1']['ccxt_payload'])
        report=CapturedOrder(json.dumps(raw),now,'synthetic-unfilled-aged','synthetic')
        e=self.account(report,cursor=2);receipt=self.receipt(e,cash_used='50')
        with r.wallet.clock.at(now):
            NativeFactorRuntime(r.gate,r.wallet).observe_entries('aged',e,receipt,self.receipts(e),{'r1':report},(),bot,now,
                complete_sequence=0,complete_from_inception=True,funding_complete=True)
            ex=self.cancel_tape(r,now,filled=0);report=r.cancel('r1',e,ex,now)
            e=self.account(report,cursor=3);receipt=self.receipt(e)
            with patch.object(r.provider,'checkpoint',side_effect=OSError('synthetic empty-entry account interruption')):
                with self.assertRaises(OSError):self.batch(r,bot,'zero-cancel',e,receipt,{'r1':report})
            self.assertIsNone(Trade.session.get(Trade,trade.id))
            with patch.object(r.cancellations,'apply_terminal',side_effect=AssertionError('zero deletion repeated')):
                result=self.batch(r,bot,'zero-cancel',e,receipt,{'r1':report})
        self.assertEqual(D(result['decision'].equity),D(1000));self.assertEqual(D(result['audit']['fees_paid']),D(0))
        self.assertEqual(r.cancellations.s['requests']['r1']['state'],'TERMINAL_RECONCILED')


class MultiCancellationBatchTests(unittest.TestCase):
    for _name in ('setUp','tearDown','provider','checkpoint','receipts','bars','setup_runtime','account','prepare_response',
                  'reports','observe','submit_open','fill_events'):
        locals()[_name]=getattr(mf.FactorRuntimeTests,_name)
    del _name

    def cycle(self,fault=False):
        c=self.submit_open(self.setup_runtime(),count=2);now=c.now+timedelta(seconds=61)
        reports=self.reports(c,now,fill=3);events=self.fill_events(c,reports,now)
        with c.wallet.clock.at(now):checked=self.observe(c,'aged-partials',reports,now,4,events)['evidence']
        rid=next(iter(reports));other=next(x for x in reports if x!=rid)
        cancel=NativeCancellation(self.root/'multi-cancel',c.gate,create=True);self.opened.append(cancel)
        raw=copy.deepcopy(c.gate.s['requests'][rid]['venue_payload']);received=now+timedelta(seconds=1)
        raw.update(status='CANCELED',executedQty='4',cummulativeQuoteQty='40',time=int(c.now.timestamp()*1000),updateTime=int(received.timestamp()*1000))
        tape=LocalBinanceTape(list(c.ex._markets.values()),[(raw,received),(raw,received)],allowed_methods=('GET','DELETE'))
        ex=LocalCancelExchange(tape,c.market);self.opened.append(ex)
        with c.wallet.clock.at(now):report=cancel.dispatch(rid,checked,ex,now)
        reports={rid:report,other:replace(reports[other],received_at=received)}
        e,receipt,quotes=self.account(c,reports,received,5);events=self.fill_events(c,{rid:report},received,start=3,increment='1')
        def batch():return c.runtime.observe_account('cancel-with-partial',e,receipt,quotes,reports,events,c.bot,received,
            complete_sequence=3,complete_from_inception=True,funding_complete=True,cancel_controller=cancel,cancel_requests=[rid])
        with c.wallet.clock.at(received):
            if fault:
                save=cancel.store.save
                def fail_after_native(state):
                    if state['requests'][rid]['state']=='NATIVE_APPLIED_ACCOUNT_PENDING':raise OSError('synthetic cancel acknowledgement loss')
                    return save(state)
                with patch.object(cancel.store,'save',side_effect=fail_after_native):
                    with self.assertRaises(StateError):batch()
                cancel.close();cancel=NativeCancellation(self.root/'multi-cancel',c.gate);self.opened.append(cancel)
                self.assertEqual(cancel.s['requests'][rid]['state'],'APPLICATION_PENDING')
                with patch.object(c.bot.strategy,'order_filled',side_effect=AssertionError('terminal cancel callback repeated')):result=batch()
            else:result=batch()
            with self.assertRaises(RiskError):c.guard.require_entry_clear()
            c.gate.require_account_batch_clear()
        self.assertEqual(cancel.s['requests'][rid]['state'],'TERMINAL_RECONCILED')
        self.assertEqual(c.gate.lifecycle.s['requests'][other]['lifecycle']['state'],'OBSERVED_OPEN')
        self.assertEqual(len(result['evidence'].snapshot.positions),2)
        self.assertEqual(D(result['decision'].equity),D('999.839'))
        self.assertEqual(D(result['audit']['fees_paid']),D('.07'))
        self.assertEqual([x['method'] for x in ex._api.requests],['DELETE','GET'])

    def test_cancel_one_f1_campaign_preserves_other_open_partial_and_complete_nav(self):self.cycle()
    def test_cancel_recovery_verifies_own_fill_while_other_campaign_stays_pending(self):self.cycle(fault=True)


if __name__=='__main__':unittest.main(verbosity=2)
