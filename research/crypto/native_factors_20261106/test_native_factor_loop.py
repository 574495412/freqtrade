"""Explicit priority dispatch, complete account feedback and native factor actions."""
import copy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'sources'))
import test_native_risk_runtime as rr
import test_native_strategy_exits as se
import test_native_position_targets as pt
import test_native_factor_runtime as mf
import test_native_entries as ef
from native_factor_loop import NativeFactorLoop
from native_factor_runtime import NativeFactorRuntime
from native_cancellations import NativeCancellation
from native_exits import NativeRiskExit,LocalExitExchange
from native_entries import NativeEntryGate,LocalBinanceTape
from native_strategy_exits import ExitBarInputs,ExitRate
from native_position_targets import PositionReductionInputs,build_position_targets
from native_evidence import decimal_text
from rd1 import RiskError,StateError,digest


def dispatch(loop,bot,now,**overrides):
    args=dict(cancel_exchanges={},exit_exchanges={},exit_rates={},strategy_inputs={},reduction_inputs=None,allow_entry=False)
    args.update(overrides);return loop.dispatch(bot,now,**args)


def attach(owner,exits,wallet):
    cancel=NativeCancellation(owner.root/'loop-cancel',exits.gate,create=True);owner.opened.append(cancel)
    return NativeFactorLoop(NativeFactorRuntime(exits.gate,wallet),cancel,exits)


class RiskLoopTests(unittest.TestCase):
    for _name in ('setUp','tearDown','provider','checkpoint','receipts','event','account','execute','receipt','boot','harness',
                  'runtime','observed','cancel_tape','terminal_account'):
        locals()[_name]=getattr(rr.NativeRiskRuntimeTests,_name)
    del _name

    def cycle(self,market):
        r,trade,bot,result,_,_,_,now=self.observed(market)
        loop=NativeFactorLoop(NativeFactorRuntime(r.gate,r.wallet),r.cancellations,r.exits)
        received=now+timedelta(seconds=1);cancel_ex=self.cancel_tape(r,received)
        with r.wallet.clock.at(now):
            first=dispatch(loop,bot,now,cancel_exchanges={'r1':cancel_ex},strategy_inputs={'irrelevant':object()})
            self.assertEqual(first['state'],'DISPATCHED_CANCEL')
            self.assertEqual(dispatch(loop,bot,now)['state'],'WAIT_ACCOUNT')
        report=r.cancellations._envelope('r1',received);e,receipt=self.terminal_account(r,trade,report)
        with r.wallet.clock.at(received):
            observed=loop.observe('loop-cancel',e,receipt,self.receipts(e),{'r1':report},[self.event(trade,2,quantity='1',fee='.01',seconds=2)],
                bot,received,complete_sequence=2,complete_from_inception=True,funding_complete=True)
            action=next(a for a in observed['decision'].actions if a['kind']=='EXIT_POSITION')
            client='rd1x-'+digest(dict(account=r.risk.s['identity'],action=action['id']))[:28]
            raw=dict(symbol='BTCUSDT',orderId=200,clientOrderId=client,time=int(received.timestamp()*1000),transactTime=int(received.timestamp()*1000),
                updateTime=int(received.timestamp()*1000),price='3',origQty='4',executedQty='0',cummulativeQuoteQty='0',cumQuote='0',avgPrice='0',
                status='NEW',timeInForce='GTC',type='LIMIT',side='SELL',reduceOnly=True)
            tape=LocalBinanceTape([ef.market_fixture(market)],[(raw,received)],allowed_methods=('POST','GET'))
            ex=LocalExitExchange(tape,r.exits);self.opened.append(ex)
            rates={} if market=='spot' else {trade.pair:ExitRate(trade.pair,'3',received,received,'synthetic-risk-bid','synthetic')}
            second=dispatch(loop,bot,received,exit_exchanges={trade.pair:ex},exit_rates=rates)
            self.assertEqual(second['state'],'DISPATCHED_EXIT');self.assertEqual(trade.amount,4)
            self.assertEqual(dispatch(loop,bot,received)['state'],'WAIT_ACCOUNT')
        now=received+timedelta(seconds=1)
        raw.update(status='FILLED',executedQty='4',cummulativeQuoteQty='12',cumQuote='12',avgPrice='3',updateTime=int(now.timestamp()*1000))
        ex._api.responses=[(raw,now)]
        with r.wallet.clock.at(now):
            ex.query(second['result'],now)
            e=self.account(r.exits.envelope(second['result'],now),balance='971.948',cursor=4,market=market)
            result=loop.observe('loop-flat',e,self.receipt(e),self.receipts(e),{},
                [self.event(trade,3,quantity='-4',fee='.012',seconds=3,order_id='200',price='3')],bot,now,
                complete_sequence=3,complete_from_inception=True,funding_complete=True)
            final=dispatch(loop,bot,now,allow_entry=True)
        self.assertFalse(trade.is_open);self.assertEqual(final['state'],'BLOCKED_RISK')
        self.assertEqual(D(result['audit']['observed_equity']),D('971.948'))
        self.assertEqual([x['method'] for x in cancel_ex._api.requests],['DELETE','GET'])
        self.assertEqual([x['method'] for x in ex._api.requests],['POST','GET'])

    def test_spot_priority_cancel_then_full_risk_exit_then_daily_loss_blocks_entry(self):self.cycle('spot')
    def test_futures_priority_uses_independent_exit_bid_and_source_account(self):self.cycle('futures')


class StrategyLoopTests(unittest.TestCase):
    for _name in ('setUp','tearDown','provider','checkpoint','receipts','event','account','execute','receipt','boot','fill_cycle','harness','position','exchange'):
        locals()[_name]=getattr(se.NativeStrategyExitTests,_name)
    del _name

    def stop(self,market='spot'):
        exits,wallet,p,guard,trade,bot,e,now=self.position(market=market);loop=attach(self,exits,wallet)
        ex,raw=self.exchange(exits,trade,now,'9.4')
        rates={} if market=='spot' else {trade.pair:ExitRate(trade.pair,'9.4',now,now,'synthetic-loop-stop-bid','synthetic')}
        with wallet.clock.at(now):
            result=dispatch(loop,bot,now,exit_exchanges={trade.pair:ex},exit_rates=rates,
                strategy_inputs={trade.pair:ExitBarInputs(None,{},'synthetic-missing-bars')})
        self.assertEqual(result['state'],'DISPATCHED_EXIT');self.assertEqual(trade.exit_reason,'stop_loss')
        self.assertEqual(len(ex._api.requests),1)
        return loop,bot,trade,ex,now

    def test_native_stop_precedes_missing_factor_allocation_and_entry(self):self.stop()
    def test_native_futures_stop_uses_explicit_bid(self):self.stop('futures')

    def test_futures_mark_only_does_not_dispatch(self):
        exits,wallet,p,guard,trade,bot,e,now=self.position(market='futures');loop=attach(self,exits,wallet)
        ex,_=self.exchange(exits,trade,now,'9.4')
        with wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError,'mark cannot substitute'):
                dispatch(loop,bot,now,exit_exchanges={trade.pair:ex},strategy_inputs={trade.pair:ExitBarInputs(None,{},'synthetic-no-bars')})
        self.assertEqual(ex._api.requests,[])

    def test_no_exit_evaluation_cannot_be_bypassed_by_changing_same_cursor_inputs(self):
        exits,wallet,p,guard,trade,bot,e,now=self.position(rate='10');loop=attach(self,exits,wallet)
        ex,_=self.exchange(exits,trade,now,'10')
        args=dict(exit_exchanges={trade.pair:ex},strategy_inputs={trade.pair:ExitBarInputs(None,{},'synthetic-no-bars')},
                  reduction_inputs=PositionReductionInputs({},'synthetic-no-factor'))
        with wallet.clock.at(now):
            self.assertEqual(dispatch(loop,bot,now,**args)['state'],'IDLE')
            self.assertEqual(dispatch(loop,bot,now,**args)['state'],'IDLE')
            args['strategy_inputs']={trade.pair:ExitBarInputs(None,{},'changed-source')}
            with self.assertRaisesRegex(StateError,'identity changed'):dispatch(loop,bot,now,**args)
        self.assertEqual(ex._api.requests,[])

    def test_parent_acknowledgement_loss_after_native_exit_cannot_repeat_post(self):
        exits,wallet,p,guard,trade,bot,e,now=self.position();loop=attach(self,exits,wallet)
        ex,_=self.exchange(exits,trade,now,'9.4');gate=exits.gate;save=gate.store.save
        def fail_after_child(state):
            if any(c['state']=='AWAIT_ACCOUNT' for c in state.get('dispatch_cycles',{}).values()):raise OSError('synthetic dispatch acknowledgement loss')
            return save(state)
        with wallet.clock.at(now):
            with patch.object(gate.store,'save',side_effect=fail_after_child):
                with self.assertRaises(StateError):dispatch(loop,bot,now,exit_exchanges={trade.pair:ex},
                    strategy_inputs={trade.pair:ExitBarInputs(None,{},'synthetic-no-bars')})
            cancel_dir=loop.cancellations.store.directory;exit_dir=exits.store.directory
            loop.cancellations.close();exits.close();gate.close()
            gate=NativeEntryGate(self.root/'spot-receipts',gate.lifecycle,after_fill_guard=guard);self.opened.append(gate);wallet.attach(gate)
            cancel=NativeCancellation(cancel_dir,gate);self.opened.append(cancel)
            exits=NativeRiskExit(exit_dir,gate,wallet);self.opened.append(exits)
            loop=NativeFactorLoop(NativeFactorRuntime(gate,wallet),cancel,exits)
            self.assertEqual(dispatch(loop,bot,now)['state'],'WAIT_ACCOUNT')
        self.assertEqual(len(ex._api.requests),1)


class ReductionLoopTests(unittest.TestCase):
    for _name in ('setUp','tearDown','provider','checkpoint','receipts','event','account','execute','receipt','boot','fill_cycle','harness','position','exchange'):
        locals()[_name]=getattr(pt.NativePositionTargetTests,_name)
    del _name

    def test_fixed_f2_daily_target_dispatches_native_partial_exit_before_entry(self):
        exits,wallet,p,guard,trade,bot,e,now,ri,si=self.position();loop=attach(self,exits,wallet)
        ex,raw=self.exchange(exits,trade,now,'1')
        with wallet.clock.at(now):
            targets,_,_=build_position_targets(p,e,bot.strategy,ri,now,{})
            target=D(decimal_text(ex.amount_to_contract_precision(trade.pair,float(targets[trade.pair]))))
            raw['origQty']=str(D(5)-target)
            result=dispatch(loop,bot,now,exit_exchanges={trade.pair:ex},strategy_inputs={trade.pair:si},reduction_inputs=ri,allow_entry=True)
        self.assertEqual(result['state'],'DISPATCHED_EXIT');self.assertEqual(trade.exit_reason,'F2_daily_scale')
        self.assertEqual(D(ex._api.requests[0]['params']['quantity']),D(5)-target)


class EntryLoopTests(unittest.TestCase):
    for _name in ('setUp','tearDown','provider','checkpoint','receipts','bars','setup_runtime','account','prepare_response','reports','fill_events'):
        locals()[_name]=getattr(mf.FactorRuntimeTests,_name)
    del _name

    def test_two_real_f1_entries_need_fresh_whole_account_between_dispatches(self):
        c=self.setup_runtime();cancel=NativeCancellation(self.root/'entry-loop-cancel',c.gate,create=True);self.opened.append(cancel)
        exits=NativeRiskExit(self.root/'entry-loop-exits',c.gate,c.wallet,create=True);self.opened.append(exits)
        loop=NativeFactorLoop(c.runtime,cancel,exits)
        kwargs=dict(entry_exchange=c.ex,entry_inputs=c.inputs,entry_liquidity=c.liquidity,allow_entry=True)
        with c.wallet.clock.at(c.now):
            for i in range(2):
                rid=self.prepare_response(c,i)
                result=dispatch(loop,c.bot,c.now,**kwargs)
                self.assertEqual(result['state'],'DISPATCHED_ENTRY');self.assertEqual(result['result']['request_id'],rid)
                self.assertEqual(dispatch(loop,c.bot,c.now,**kwargs)['state'],'WAIT_ACCOUNT')
                reports=self.reports(c,c.now);e,receipt,quotes=self.account(c,reports,c.now,i+2)
                loop.observe('loop-open-'+str(i),e,receipt,quotes,reports,(),c.bot,c.now,
                    complete_sequence=0,complete_from_inception=True,funding_complete=True)
        now=c.now+timedelta(seconds=1);reports=self.reports(c,now,fill=3);events=self.fill_events(c,reports,now)
        e,receipt,quotes=self.account(c,reports,now,4)
        with c.wallet.clock.at(now):
            loop.observe('loop-partials',e,receipt,quotes,reports,events,c.bot,now,
                complete_sequence=2,complete_from_inception=True,funding_complete=True)
            self.assertEqual(dispatch(loop,c.bot,now,**kwargs)['state'],'WAIT_ORDER')
        self.assertEqual(len(c.ex._api.requests),2)


if __name__=='__main__':unittest.main(verbosity=2)
