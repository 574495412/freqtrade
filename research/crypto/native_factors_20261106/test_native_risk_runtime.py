"""Continuous native partial/cancel/exit flow, synthetic events and real SQLite."""
import copy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import json
from pathlib import Path
import sys
from types import MethodType
import unittest
from unittest.mock import Mock, patch

import ccxt
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE/'sources'))
from native_risk_runtime import NativeRiskRuntime
from native_cancellations import NativeCancellation, LocalCancelExchange
from native_exits import NativeRiskExit, LocalExitExchange
from native_after_fill import OfflineProtectionExchange
from native_entries import LocalBinanceTape
from native_evidence import CapturedOrder
from rd1 import RiskError, StateError, digest
from freqtrade.persistence import Trade
from freqtrade.freqtradebot import FreqtradeBot
from freqtrade.exceptions import TemporaryError
from test_native_factors import resolved
import test_native_entries as ef
import test_native_wallets as wf
import test_native_account as af


class NativeRiskRuntimeTests(unittest.TestCase):
    setUp = ef.NativeEntryTests.setUp
    tearDown = ef.NativeEntryTests.tearDown
    provider = wf.NativeWalletTests.provider
    checkpoint = wf.NativeWalletTests.checkpoint
    receipts = wf.NativeWalletTests.receipts
    event = wf.NativeWalletTests.event
    account = wf.NativeWalletTests.account
    execute = wf.NativeWalletTests.execute
    receipt = wf.NativeWalletTests.receipt
    boot = wf.NativeWalletTests.boot
    harness = wf.NativeWalletTests.harness

    def runtime(self, market='spot'):
        life,gate,ex,bot,e,p,provider,_,guard,wallet=self.harness(market)
        e=replace(e,source_id=af.SOURCE,snapshot=replace(e.snapshot,cursor=1))
        with wallet.clock.at(ef.T):
            bot.wallets=self.boot(provider,wallet,e,'entry')
            self.execute(ex,bot,e,p)
            trade=Trade.get_trades_proxy()[0];gate.bind_native('r1')
        tiers={p.pair:[{'minNotional':0,'maxNotional':50000,'maintenanceMarginRate':.004,'maintAmt':0}]} if market=='futures' else {}
        offline=OfflineProtectionExchange(market,[ef.market_fixture(market)],tiers,'synthetic-rules');self.opened.append(offline)
        bot.exchange=offline;bot.strategy=resolved('F1Momentum168Native',(p.pair,));bot.config['dry_run']=True;bot.margin_mode=offline.margin_mode
        bot.order_close_notify=Mock()
        for name in ('order_obj_or_raise','handle_order_fee','get_real_amount','apply_fee_conditional','cancel_stoploss_on_exchange'):
            setattr(bot,name,MethodType(getattr(FreqtradeBot,name),bot))
        cancel=NativeCancellation(self.root/'cancel-runtime',gate,create=True);self.opened.append(cancel)
        exits=NativeRiskExit(self.root/'exit-runtime',gate,wallet,create=True);self.opened.append(exits)
        runtime=NativeRiskRuntime(gate,wallet,cancel,exits)
        return runtime,trade,bot

    def observed(self, market='spot'):
        runtime,trade,bot=self.runtime(market)
        now=ef.T+timedelta(seconds=1)
        raw=copy.deepcopy(runtime.gate.s['requests']['r1']['ccxt_payload'])
        raw.update(filled=3.,remaining=2.,average=10.,cost=30.)
        report=CapturedOrder(json.dumps(raw),now,'synthetic-open-partial','synthetic')
        runtime.provider.append(self.event(trade,quantity='3',fee='.03'))
        e=self.account(report,trade,'3','969.97' if market=='spot' else '999.97',market=market)
        e=replace(e,snapshot=replace(e.snapshot,quotes=tuple(replace(q,price=D(3)) if q.pair==trade.pair else q for q in e.snapshot.quotes),
            positions=tuple(replace(p,collateral=D(30) if market=='futures' else D(0)) for p in e.snapshot.positions)))
        receipt=self.receipt(e,quantity='3',cash_used='20' if market=='spot' else '50',collateral='30',upnl='-21')
        with runtime.wallet.clock.at(now):
            result=runtime.observe_partial('r1',report,e,receipt,self.receipts(e),bot,now,complete_sequence=1,complete_from_inception=True,funding_complete=True)
        return runtime,trade,bot,result,report,e,receipt,now

    def cancel_tape(self,runtime,now,*,status='CANCELED',filled=4):
        raw=copy.deepcopy(runtime.gate.s['requests']['r1']['venue_payload'])
        raw.update(status=status,executedQty=str(filled),cummulativeQuoteQty=str(filled*10),cumQuote=str(filled*10),avgPrice='10',time=int(ef.T.timestamp()*1000),updateTime=int(now.timestamp()*1000))
        delete=copy.deepcopy(raw)
        if runtime.risk.market=='spot':
            delete.update(origClientOrderId=raw['clientOrderId'],clientOrderId='synthetic-cancel-alias')
            raw['clientOrderId']='synthetic-cancel-alias'
        tape=LocalBinanceTape([ef.market_fixture(runtime.risk.market)],[(delete,now),(raw,now)],allowed_methods=('GET','DELETE'))
        ex=LocalCancelExchange(tape,runtime.risk.market);self.opened.append(ex)
        return ex

    def terminal_account(self,runtime,trade,report):
        market=runtime.risk.market
        e=self.account(report,trade,'4','959.96' if market=='spot' else '999.96',cursor=3,market=market)
        e=replace(e,snapshot=replace(e.snapshot,quotes=tuple(replace(q,price=D(3)) if q.pair==trade.pair else q for q in e.snapshot.quotes),
            positions=tuple(replace(p,collateral=D(40) if market=='futures' else D(0)) for p in e.snapshot.positions)))
        receipt=self.receipt(e,quantity='4',cash_used='0' if market=='spot' else '40',collateral='40',upnl='-28')
        return e,receipt

    def canceled(self,market='spot',fault=False):
        runtime,trade,bot,result,_,_,_,now=self.observed(market)
        received=now+timedelta(seconds=1);ex=self.cancel_tape(runtime,received)
        with runtime.wallet.clock.at(now):
            report=runtime.cancel('r1',result['evidence'],ex,now)
        runtime.provider.append(self.event(trade,2,quantity='1',fee='.01',seconds=2))
        e,receipt=self.terminal_account(runtime,trade,report)
        kwargs=dict(complete_sequence=2,complete_from_inception=True,funding_complete=True)
        with runtime.wallet.clock.at(received):
            if fault:
                with patch.object(runtime.provider,'checkpoint',side_effect=OSError('synthetic account boundary failure')):
                    with self.assertRaises(OSError):runtime.finish_cancel('r1',e,receipt,self.receipts(e),bot,received,**kwargs)
                self.assertEqual(runtime.cancellations.s['requests']['r1']['state'],'NATIVE_APPLIED_ACCOUNT_PENDING')
                with self.assertRaisesRegex(RiskError,'pending'):runtime.wallet.require_entry_clear(received)
                runtime.cancellations.close()
                c=NativeCancellation(self.root/'cancel-runtime',runtime.gate);self.opened.append(c)
                runtime=NativeRiskRuntime(runtime.gate,runtime.wallet,c,runtime.exits)
                with patch.object(bot.strategy,'order_filled',side_effect=AssertionError('callback must not repeat')):
                    result=runtime.finish_cancel('r1',e,receipt,self.receipts(e),bot,received,**kwargs)
            else:
                result=runtime.finish_cancel('r1',e,receipt,self.receipts(e),bot,received,**kwargs)
            runtime.guard.require_entry_clear()
        self.assertEqual(result['lifecycle']['state'],'TERMINAL_RECONCILED')
        self.assertEqual(trade.amount,4)
        self.assertEqual(trade.stop_loss,9.5)
        self.assertEqual(result['audit']['fees_paid'],'0.04')
        self.assertEqual([x['method'] for x in ex._api.requests],['DELETE','GET'])
        return runtime,trade,bot,result,received,ex

    def full_cycle(self,market,*,fault=False):
        runtime,trade,bot,result,now,cancel_ex=self.canceled(market,fault=fault)
        action=next(a for a in result['decision'].actions if a['kind']=='EXIT_POSITION')
        client='rd1x-'+digest({'account':runtime.risk.s['identity'],'action':action['id']})[:28]
        raw={'symbol':'BTCUSDT','orderId':200,'clientOrderId':client,'time':int(now.timestamp()*1000),'transactTime':int(now.timestamp()*1000),
            'updateTime':int(now.timestamp()*1000),'price':'3','origQty':'4','executedQty':'0','cummulativeQuoteQty':'0','cumQuote':'0','avgPrice':'0',
            'status':'NEW','timeInForce':'GTC','type':'LIMIT','side':'SELL','reduceOnly':True}
        tape=LocalBinanceTape([ef.market_fixture(market)],[(raw,now)],allowed_methods=('POST','GET'))
        ex=LocalExitExchange(tape,runtime.exits);self.opened.append(ex)
        with runtime.wallet.clock.at(now):aid=runtime.exit_after_cancel('r1',result['evidence'],ex,bot,now,limit='3')
        self.assertEqual(ex._api.requests[0]['params']['quantity'],'4')
        now+=timedelta(seconds=1)
        raw=copy.deepcopy(raw);raw.update(status='FILLED',executedQty='4',cummulativeQuoteQty='12',cumQuote='12',avgPrice='3',updateTime=int(now.timestamp()*1000))
        ex._api.responses=[(raw,now)]
        with runtime.wallet.clock.at(now):ex.query(aid,now)
        runtime.provider.append(self.event(trade,3,quantity='-4',fee='.012',seconds=3,order_id='200',price='3'))
        e=self.account(runtime.exits.envelope(aid,now),balance='971.948',cursor=4,market=market)
        with runtime.wallet.clock.at(now):
            bot.wallets=runtime.wallet.stage('exit-flat',self.receipt(e),e,now)
            runtime.exits.apply_filled(aid,bot,now)
            checked,audit=self.checkpoint(runtime.provider,e);runtime.wallet.confirm(checked,now)
            final=runtime.exits.reconcile(aid,checked,now)
        self.assertFalse(trade.is_open)
        self.assertEqual(D(audit['observed_equity']),D('971.948'))
        self.assertEqual(D(audit['fees_paid']),D('.052'))
        self.assertFalse(final['requires_further_exit'])
        self.assertIn('daily_loss',runtime.risk.s['latches'])
        self.assertEqual(len(cancel_ex._api.requests),2)
        return runtime

    def test_spot_open_partial_cancel_race_and_risk_exit_share_one_account(self):
        self.full_cycle('spot')

    def test_futures_open_partial_collateral_cancel_race_and_reduce_only_exit(self):
        self.full_cycle('futures')

    def test_pending_partial_blocks_entry_but_yields_cancel_and_exit_intents(self):
        runtime,trade,bot,result,report,e,receipt,now=self.observed()
        self.assertEqual(trade.amount,0)
        self.assertTrue(trade.orders[0].ft_is_open)
        self.assertEqual(result['lifecycle']['state'],'OBSERVED_OPEN')
        self.assertEqual(result['protection']['state'],'OPEN_PARTIAL_PENDING')
        self.assertEqual({a['kind'] for a in result['decision'].actions},{'CANCEL_ENTRY','EXIT_POSITION'})
        self.assertLess((now-ef.T).total_seconds(),60)
        with runtime.wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError,'Unresolved'):runtime.guard.require_entry_clear()
            with self.assertRaisesRegex(StateError,'not terminally'):runtime.exit_after_cancel('r1',result['evidence'],None,bot,now,limit='3')
            with patch.object(bot.strategy,'order_filled',side_effect=AssertionError('open order cannot invoke terminal callback')):
                again=runtime.observe_partial('r1',report,e,receipt,self.receipts(e),bot,now,complete_sequence=1,complete_from_inception=True,funding_complete=True)
            self.assertEqual(again['protection']['state'],'OPEN_PARTIAL_PENDING')

    def test_committed_cancel_resume_after_account_failure_does_not_repeat_callbacks_or_delete(self):
        self.full_cycle('spot',fault=True)

    def test_open_cancel_receipt_never_materializes_or_exits_partial_inventory(self):
        runtime,trade,bot,result,_,_,_,now=self.observed()
        received=now+timedelta(seconds=1);ex=self.cancel_tape(runtime,received,status='PARTIALLY_FILLED',filled=3)
        with runtime.wallet.clock.at(now):report=runtime.cancel('r1',result['evidence'],ex,now)
        e,receipt=self.terminal_account(runtime,trade,report)
        with runtime.wallet.clock.at(received):
            with self.assertRaisesRegex(RiskError,'remains open'):
                runtime.finish_cancel('r1',e,receipt,self.receipts(e),bot,received,complete_sequence=1,complete_from_inception=True,funding_complete=True)
            with self.assertRaisesRegex(StateError,'not terminally'):runtime.exit_after_cancel('r1',result['evidence'],None,bot,received,limit='3')
        self.assertEqual(trade.amount,0)
        self.assertTrue(trade.orders[0].ft_is_open)

    def test_missing_cancel_race_fill_blocks_native_application_and_risk_release(self):
        runtime,trade,bot,result,_,_,_,now=self.observed()
        received=now+timedelta(seconds=1);ex=self.cancel_tape(runtime,received)
        with runtime.wallet.clock.at(now):report=runtime.cancel('r1',result['evidence'],ex,now)
        e,receipt=self.terminal_account(runtime,trade,report)
        with runtime.wallet.clock.at(received):
            with self.assertRaisesRegex(RiskError,'misses cumulative'):
                runtime.finish_cancel('r1',e,receipt,self.receipts(e),bot,received,complete_sequence=1,complete_from_inception=True,funding_complete=True)
        self.assertEqual(trade.amount,0)
        self.assertNotEqual(runtime.gate.lifecycle.s['requests']['r1']['lifecycle']['state'],'TERMINAL_RECONCILED')

    def test_lost_cancel_reply_reopens_and_queries_without_second_delete(self):
        runtime,trade,bot,result,_,_,_,now=self.observed('futures')
        received=now+timedelta(seconds=1);ex=self.cancel_tape(runtime,received)
        answer=copy.deepcopy(ex._api.responses[-1])
        ex._api.responses=[ccxt.RequestTimeout('synthetic lost cancellation response')]
        with runtime.wallet.clock.at(now):
            with self.assertRaises(TemporaryError):runtime.cancel('r1',result['evidence'],ex,now)
        runtime.cancellations.close()
        c=NativeCancellation(self.root/'cancel-runtime',runtime.gate);self.opened.append(c)
        runtime=NativeRiskRuntime(runtime.gate,runtime.wallet,c,runtime.exits)
        with runtime.wallet.clock.at(received):
            with self.assertRaisesRegex(StateError,'already attempted'):runtime.cancel('r1',result['evidence'],ex,received)
            ex._api.responses=[answer]
            report=runtime.query_cancel('r1',ex,received)
        runtime.provider.append(self.event(trade,2,quantity='1',fee='.01',seconds=2))
        e,receipt=self.terminal_account(runtime,trade,report)
        with runtime.wallet.clock.at(received):
            result=runtime.finish_cancel('r1',e,receipt,self.receipts(e),bot,received,
                complete_sequence=2,complete_from_inception=True,funding_complete=True)
        self.assertEqual(result['lifecycle']['state'],'TERMINAL_RECONCILED')
        self.assertEqual(trade.amount,4)
        self.assertEqual([r['method'] for r in ex._api.requests],['DELETE','GET'])

    def test_explicit_partial_replay_rejects_native_inventory_mutation(self):
        runtime,trade,bot,_,report,e,receipt,now=self.observed()
        trade.stop_loss=8.;Trade.commit()
        with runtime.wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError,'Pending partial native state changed'):
                runtime.observe_partial('r1',report,e,receipt,self.receipts(e),bot,now,
                    complete_sequence=1,complete_from_inception=True,funding_complete=True)
        self.assertEqual(runtime.guard.s['events'][next(iter(runtime.guard.s['events']))]['state'],'OPEN_PARTIAL_PENDING')

    def test_captured_receipt_cannot_be_relabelled_as_synthetic_partial(self):
        runtime,trade,bot,_,report,e,receipt,now=self.observed()
        sequence=runtime.guard.s['sequence']
        with runtime.wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError,'explicit synthetic source'):
                runtime.observe_partial('r1',replace(report,evidence_class='captured'),e,receipt,self.receipts(e),bot,now,
                    complete_sequence=1,complete_from_inception=True,funding_complete=True)
        self.assertEqual(runtime.guard.s['sequence'],sequence)
        self.assertEqual(trade.amount,0)


if __name__=='__main__':unittest.main()
