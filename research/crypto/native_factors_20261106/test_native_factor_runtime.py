"""Multi-pair initial orders, batched native fills and one full account NAV."""
import copy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import json
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import numpy as np
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'sources'))
import test_native_entries as ef
import test_native_account as af
from test_native_factors import resolved
import test_native_factor_entries as fef
from native_factor_runtime import NativeFactorRuntime
from native_factor_entries import EntryInputs, EntryLiquidity, size_entry, fixed_entry_signal
from native_wallets import NativeWalletBridge, WalletReceipt
from native_after_fill import AfterFillGuard, OfflineProtectionExchange
from native_entries import NativeEntryGate, LocalBinanceTape
from native_evidence import CapturedOrder
from native_account import QuoteReceipt
from rd1 import Position, Order, Quote, RiskError, StateError, digest, stamp
from freqtrade.freqtradebot import FreqtradeBot
from freqtrade.persistence import Trade


class FactorRuntimeTests(unittest.TestCase):
    setUp=ef.NativeEntryTests.setUp
    tearDown=ef.NativeEntryTests.tearDown
    provider=af.NativeAccountTests.provider
    checkpoint=af.NativeAccountTests.checkpoint
    receipts=af.NativeAccountTests.receipts
    bars=fef.FixedEntryTests.bars

    def setup_runtime(self,market='spot'):
        life,gate,ex,bot,e,p,provider,start=af.NativeAccountTests.harness(self,market)
        universe=tuple(life.risk.plan['markets'][market]['pairs'])
        markets=[]
        for pair in universe:
            m=ef.market_fixture(market);base=pair.split('/')[0]
            m.update(id=base+'USDT',symbol=pair,base=base,baseId=base)
            markets.append(m)
        ex._api.close();ex._api=LocalBinanceTape(markets,[]);ex._markets=ex._api.markets;del ex.get_fee
        tiers={pair:[dict(minNotional=0,maxNotional=50000,maxLeverage=1,maintenanceMarginRate=.004,maintAmt=0)] for pair in universe} if market=='futures' else {}
        ex._leverage_tiers=tiers
        guard=AfterFillGuard(self.root/'batch-protection',life.risk,create=True);self.opened.append(guard);gate.after_fill_guard=guard
        wallet=NativeWalletBridge(self.root/'batch-wallet',provider,markets,create=True);self.opened.append(wallet);wallet.attach(gate)
        offline=OfflineProtectionExchange(market,markets,tiers,'synthetic-multi-pair-rules');self.opened.append(offline)
        strategy=resolved('F1Momentum168Native',universe)
        bot.exchange=offline;bot.strategy=strategy;bot.config['dry_run']=True;bot.margin_mode=offline.margin_mode;bot.order_close_notify=Mock()
        for name in ('order_obj_or_raise','handle_order_fee','get_real_amount','apply_fee_conditional','cancel_stoploss_on_exchange'):
            setattr(bot,name,MethodType(getattr(FreqtradeBot,name),bot))
        now=ef.T.replace(hour=0,minute=5,second=0,microsecond=0)
        day=pd.Timestamp(now).normalize();dates=pd.date_range(end=day-pd.Timedelta(hours=1),periods=169,freq='h')
        hours={pair:self.bars(dates,10*np.exp(np.arange(169)*(.002 if i==0 else .001)+.01*np.sin(np.arange(169)))) for i,pair in enumerate(universe)}
        main=self.bars(pd.DatetimeIndex([day]),[10.]);bars=EntryInputs(main,hours,'synthetic-batch-bars')
        inputs={pair:bars for pair in reversed(universe)}  # deliberately reverse input insertion order
        liquidity={pair:EntryLiquidity(pair,'10','10','100000','100000',now.replace(minute=0)-timedelta(hours=1),now,now,
            'synthetic-batch-book','synthetic','synthetic-batch-rules',now-timedelta(days=1),now+timedelta(days=1)) for pair in universe}
        c=SimpleNamespace(life=life,gate=gate,ex=ex,bot=bot,e=e,provider=provider,wallet=wallet,guard=guard,strategy=strategy,
                          universe=universe,now=now,inputs=inputs,liquidity=liquidity,runtime=NativeFactorRuntime(gate,wallet),market=market)
        with wallet.clock.at(now):
            evidence,receipt,quotes=self.account(c,{},now,1)
            c.runtime.observe_entries('boot',evidence,receipt,quotes,{},(),bot,now,complete_sequence=0,complete_from_inception=True,funding_complete=True)
        return c

    def account(self,c,reports,now,cursor,*,cash=None):
        positions=[];orders=[];bindings={};order_bindings={};envelopes=[];base_fees={};qty_by_pair={}
        invested=D(0);fees=D(0);reserved=D(0)
        for rid,report in reports.items():
            b=c.gate.s['requests'][rid]['native_binding'];raw=json.loads(report.payload_json)
            pair=b['pair'];qty=D(str(raw['filled']));remaining=D(str(raw['remaining']));pid='campaign-'+str(b['trade_id'])
            if qty:
                invested+=qty*10;fees+=qty*D('.01');qty_by_pair[pair]=qty
                positions.append(Position(pid,pair,qty,D(10),D(0),collateral=qty*10 if c.market=='futures' else D(0)))
                bindings[pid]=b['trade_id']
            if raw['status']=='open':
                oid='source-'+pair+':'+raw['id'];created=c.now
                orders.append(Order(oid,pair,remaining,D(10),created,True));reserved+=remaining*10
                order_bindings[oid]=(pair,raw['id']);envelopes.append(report)
                if qty:base_fees[oid]='0'
        balance=D(1000)-fees-(invested if c.market=='spot' else 0) if cash is None else D(cash)
        quotes=tuple(Quote(pair,D(10),now,'spot_bid' if c.market=='spot' else 'futures_mark') for pair in c.universe)
        snap=ef.snapshot(c.market,at=now,cursor=cursor,balance=str(balance),positions=positions,orders=orders,quotes=quotes)
        e=replace(c.e,source_id=af.SOURCE,snapshot=snap,position_bindings=bindings,order_bindings=order_bindings,
                  order_envelopes=tuple(envelopes),partial_base_fees=base_fees)
        used=reserved+(invested if c.market=='futures' else 0)
        balances={'USDT':dict(free=str(balance-used),used=str(used),total=str(balance))};futures=[]
        for pair,qty in qty_by_pair.items():
            if c.market=='spot':balances[pair.split('/')[0]]=dict(free=str(qty),used='0',total=str(qty))
            else:futures.append(dict(symbol=pair,side='long',contracts=str(qty),leverage='1',initialMargin=str(qty*10),unrealizedPnl='0',entryPrice='10'))
        receipt=WalletReceipt(e.account_id,c.market,'synthetic-multi-wallet','synthetic',now,now,now,snap.balance_semantics,balances,tuple(futures),True,True)
        quote_receipts=tuple(QuoteReceipt(q,now,'synthetic-multi-quote','synthetic') for q in quotes)
        return e,receipt,quote_receipts

    def prepare_response(self,c,index):
        pair=c.universe[index]
        signal,_=fixed_entry_signal(c.strategy,pair,c.inputs[pair],c.now,c.universe)
        proposal,_=size_entry(c.provider,c.wallet._checked_evidence,c.ex,c.wallet.wallets,c.liquidity[pair],signal[1],c.now)
        self.assertIsNotNone(proposal)
        key=digest([type(c.strategy).__name__,stamp(c.now)]);rid='factor-'+digest([key,pair])[:28]
        client='rd1-'+digest({'account':c.life.s['identity'],'request':rid})[:28]
        raw=dict(symbol=pair.split('/')[0]+'USDT',orderId=100,clientOrderId=client,transactTime=int(c.now.timestamp()*1000),
                 updateTime=int(c.now.timestamp()*1000),price='10',origQty=str(proposal.quantity),executedQty='0',
                 cummulativeQuoteQty='0',cumQuote='0',avgPrice='0',status='NEW',timeInForce='GTC',type='LIMIT',side='BUY')
        c.ex._api.responses=[(raw,c.now)]
        return rid

    def reports(self,c,now,fill=None):
        reports={}
        for rid,item in c.gate.s['requests'].items():
            raw=copy.deepcopy(item['ccxt_payload']);qty=D(0) if fill is None else min(D(str(raw['amount'])),D(str(fill)))
            raw.update(filled=float(qty),remaining=float(D(str(raw['amount']))-qty),status='closed' if qty==D(str(raw['amount'])) else 'open',
                       average=10. if qty else None,cost=float(qty*10))
            reports[rid]=CapturedOrder(json.dumps(raw),now,'synthetic-multi-order-reports','synthetic')
        return reports

    def observe(self,c,batch,reports,now,cursor,events=()):
        e,receipt,quotes=self.account(c,reports,now,cursor)
        return c.runtime.observe_entries(batch,e,receipt,quotes,reports,events,c.bot,now,
             complete_sequence=c.provider.ledger.last_sequence+sum(x['event_id'] not in {v['event_id'] for v in c.provider.s['events']} for x in events),
             complete_from_inception=True,funding_complete=True)

    def submit_open(self,c,count=3):
        with c.wallet.clock.at(c.now):
            for i in range(count):
                rid=self.prepare_response(c,i)
                result=c.runtime.entry_step(c.strategy,c.inputs,c.liquidity,c.ex,c.now)
                self.assertEqual(result['pair'],c.universe[i]);self.assertEqual(result['request_id'],rid)
                with self.assertRaises(RiskError):c.runtime.entry_step(c.strategy,c.inputs,c.liquidity,c.ex,c.now)
                self.observe(c,'open-'+str(i),self.reports(c,c.now),c.now,i+2)
            self.assertEqual(len(c.ex._api.requests),count)
        return c

    def fill_events(self,c,reports,now,*,start=1,increment=None):
        events=[]
        for i,(rid,report) in enumerate(reports.items(),start):
            raw=json.loads(report.payload_json);b=c.gate.s['requests'][rid]['native_binding'];qty=D(str(raw['filled'])) if increment is None else D(increment)
            events.append(dict(event_id='event-'+str(i),sequence=i,event_at=stamp(now),received_at=stamp(now),kind='fill',
                source_id=af.SOURCE,evidence_class='synthetic',account_id=c.e.account_id,market=c.market,pair=b['pair'],
                campaign_id='campaign-'+str(b['trade_id']),native_trade_id=b['trade_id'],order_id=raw['id'],fill_id='fill-'+str(i),
                signed_quantity=str(qty),price='10',fee_quote=str(qty*D('.01')),fee_base='0'))
        return events

    def cycle(self,market):
        c=self.submit_open(self.setup_runtime(market))
        now=c.now+timedelta(seconds=1);reports=self.reports(c,now,fill=100);events=self.fill_events(c,reports,now)
        with c.wallet.clock.at(now):
            result=self.observe(c,'all-filled',reports,now,5,events)
            c.wallet.require_entry_clear(now);c.guard.require_entry_clear()
            self.assertEqual(len(result['evidence'].snapshot.positions),3)
            self.assertEqual(result['protection'],{rid:'VERIFIED' for rid in reports})
            fees=sum(D(e['fee_quote']) for e in events)
            self.assertEqual(D(result['audit']['fees_paid']),fees)
            invested=sum(D(e['signed_quantity'])*10 for e in events)
            self.assertEqual(D(result['decision'].equity),D(1000)-fees-invested*c.provider.exit_rate)
            self.assertTrue(all(t.amount>0 and t.stop_loss==9.5 for t in Trade.get_trades_proxy()))
            self.assertTrue(all(x['lifecycle']['state']=='TERMINAL_RECONCILED' for x in c.life.s['requests'].values()))
            self.assertIsNone(c.gate.s['pending_account_batch'])
        return c

    def test_spot_three_fixed_entries_share_one_account_and_batched_fill_nav(self):self.cycle('spot')
    def test_futures_three_fixed_entries_separate_cash_collateral_and_fee_nav(self):self.cycle('futures')

    def test_partial_on_one_pair_blocks_new_entry_even_when_other_pair_fully_filled(self):
        c=self.submit_open(self.setup_runtime(),count=2);now=c.now+timedelta(seconds=1)
        reports=self.reports(c,now,fill=100);rid=next(iter(reports));raw=json.loads(reports[rid].payload_json)
        raw.update(status='open',filled=3.,remaining=raw['amount']-3,average=10.,cost=30.)
        reports[rid]=replace(reports[rid],payload_json=json.dumps(raw));events=self.fill_events(c,reports,now)
        with c.wallet.clock.at(now):
            result=self.observe(c,'mixed',reports,now,4,events)
            self.assertEqual(result['protection'][rid],'OPEN_PARTIAL_PENDING')
            self.assertEqual(len(result['evidence'].snapshot.positions),2)
            with self.assertRaises(RiskError):c.guard.require_entry_clear()
            with self.assertRaises(RiskError):c.runtime.entry_step(c.strategy,c.inputs,c.liquidity,c.ex,now)
            self.assertEqual(len(c.ex._api.requests),2)

    def test_missing_open_report_and_inconsistent_same_bar_inputs_do_not_advance(self):
        c=self.submit_open(self.setup_runtime(),count=2)
        with c.wallet.clock.at(c.now):
            changed=dict(c.inputs);pair=c.universe[0];changed[pair]=replace(changed[pair],source_id='changed-source')
            with self.assertRaisesRegex(StateError,'signal inputs changed'):
                c.runtime.entry_step(c.strategy,changed,c.liquidity,c.ex,c.now)
            reports=self.reports(c,c.now);reports.pop(next(iter(reports)))
            e,r,q=self.account(c,reports,c.now,4)
            with self.assertRaisesRegex(RiskError,'every unresolved'):
                c.runtime.observe_entries('incomplete',e,r,q,reports,(),c.bot,c.now,complete_sequence=0,complete_from_inception=True,funding_complete=True)
            with self.assertRaisesRegex(RiskError,'pending'):c.wallet.require_entry_clear(c.now)
            self.observe(c,'corrected-open',self.reports(c,c.now),c.now,4)
            c.wallet.require_entry_clear(c.now)
            self.assertEqual(len(c.ex._api.requests),2)

    def test_checkpoint_failure_after_all_native_updates_resumes_without_fill_callbacks(self):
        c=self.submit_open(self.setup_runtime(),count=2);now=c.now+timedelta(seconds=1)
        reports=self.reports(c,now,fill=100);events=self.fill_events(c,reports,now)
        with c.wallet.clock.at(now):
            with patch.object(c.provider,'checkpoint',side_effect=OSError('synthetic checkpoint failure')):
                with self.assertRaises(OSError):self.observe(c,'recover',reports,now,4,events)
            self.assertTrue(all(t.amount>0 for t in Trade.get_trades_proxy()))
            self.assertEqual(c.gate.s['pending_account_batch'],'recover')
            with self.assertRaisesRegex(RiskError,'pending whole-account'):c.gate.require_account_batch_clear()
            with patch.object(c.strategy,'order_filled',side_effect=AssertionError('callback must not repeat')):
                result=self.observe(c,'recover',reports,now,4,events)
            self.assertEqual(result['audit']['event_sequence'],2)
            self.assertIsNone(c.gate.s['pending_account_batch'])
            c.guard.require_entry_clear()

    def test_half_applied_batch_ack_loss_reopens_and_only_applies_remaining_native_fill(self):
        c=self.submit_open(self.setup_runtime(),count=2);now=c.now+timedelta(seconds=1)
        reports=self.reports(c,now,fill=100);events=self.fill_events(c,reports,now)
        with c.wallet.clock.at(now):
            save=c.gate.store.save
            def ack_lost(value):
                save(value)
                if len(value.get('account_batches',{}).get('half',{}).get('applied',{}))==1:
                    raise OSError('synthetic first native application acknowledgement lost')
            with patch.object(c.gate.store,'save',side_effect=ack_lost):
                with self.assertRaises(StateError):self.observe(c,'half',reports,now,4,events)
            trades=Trade.get_trades_proxy();self.assertGreater(trades[0].amount,0);self.assertEqual(trades[1].amount,0)
            c.gate.close()
            gate=NativeEntryGate(self.root/'spot-receipts',c.life,after_fill_guard=c.guard);self.opened.append(gate)
            c.wallet.attach(gate);c.ex.gate=gate;c.gate=gate;c.runtime=NativeFactorRuntime(gate,c.wallet)
            with self.assertRaisesRegex(RiskError,'pending whole-account'):gate.require_account_batch_clear()
            with self.assertRaisesRegex(RiskError,'pending whole-account'):
                gate.submit(c.ex,'bypass',c.wallet._checked_evidence,ef.entry(),now)
            with patch.object(c.strategy,'order_filled',wraps=c.strategy.order_filled) as callback:
                self.observe(c,'half',reports,now,4,events)
            self.assertEqual([call.kwargs['trade'].pair for call in callback.call_args_list],[c.universe[1]])
            self.assertIsNone(gate.s['pending_account_batch']);c.guard.require_entry_clear()

    def test_partial_terminal_followup_preserves_other_completed_campaign_and_all_fees(self):
        c=self.submit_open(self.setup_runtime(),count=2);now=c.now+timedelta(seconds=1)
        reports=self.reports(c,now,fill=100);rid=next(iter(reports));raw=json.loads(reports[rid].payload_json);total=D(str(raw['amount']))
        raw.update(status='open',filled=3.,remaining=float(total-3),average=10.,cost=30.)
        reports[rid]=replace(reports[rid],payload_json=json.dumps(raw));events=self.fill_events(c,reports,now)
        with c.wallet.clock.at(now):self.observe(c,'mixed-first',reports,now,4,events)
        later=now+timedelta(seconds=1);all_reports=self.reports(c,later,fill=100)
        events=self.fill_events(c,{rid:all_reports[rid]},later,start=3,increment=str(total-3))
        e,receipt,quotes=self.account(c,all_reports,later,5)
        with c.wallet.clock.at(later):
            result=c.runtime.observe_entries('finish-partial',e,receipt,quotes,{rid:all_reports[rid]},events,c.bot,later,
                complete_sequence=3,complete_from_inception=True,funding_complete=True)
            c.guard.require_entry_clear();c.wallet.require_entry_clear(later)
            self.assertEqual(len(result['evidence'].snapshot.positions),2)
            self.assertTrue(all(x['lifecycle']['state']=='TERMINAL_RECONCILED' for x in c.life.s['requests'].values()))
            self.assertEqual(D(result['audit']['fees_paid']),sum(D(x['fee_quote']) for x in c.provider.s['events']))

    def test_risk_blocked_entry_sizing_receipt_is_serializable(self):
        c=self.setup_runtime()
        with c.wallet.clock.at(c.now):
            checked=c.wallet._checked_evidence
            invalid=replace(checked,snapshot=replace(checked.snapshot,funding_complete=False))
            proposal,proof=size_entry(c.provider,invalid,c.ex,c.wallet.wallets,c.liquidity[c.universe[0]],D(1),c.now)
            self.assertIsNone(proposal);self.assertEqual(proof['reason'],'rd1_entry_blocked')
            json.dumps(proof)

    def test_schedule_commit_ack_loss_before_factor_preparation_resumes_one_first_post(self):
        c=self.setup_runtime()
        with c.wallet.clock.at(c.now):
            rid=self.prepare_response(c,0);save=c.gate.store.save
            def ack_lost(value):
                save(value)
                if any(rid in s['requests'].values() for s in value.get('factor_schedules',{}).values()):
                    raise OSError('synthetic schedule-only acknowledgement lost')
            with patch.object(c.gate.store,'save',side_effect=ack_lost):
                with self.assertRaises(StateError):c.runtime.entry_step(c.strategy,c.inputs,c.liquidity,c.ex,c.now)
            self.assertEqual(c.ex._api.requests,[]);self.assertNotIn(rid,c.life.s['requests'])
            c.gate.close();gate=NativeEntryGate(self.root/'spot-receipts',c.life,after_fill_guard=c.guard)
            self.opened.append(gate);c.wallet.attach(gate);c.ex.gate=gate;c.gate=gate;c.runtime=NativeFactorRuntime(gate,c.wallet)
            result=c.runtime.entry_step(c.strategy,c.inputs,c.liquidity,c.ex,c.now)
            self.assertEqual(result['request_id'],rid)
            self.assertEqual(len(c.ex._api.requests),1)


if __name__=='__main__':unittest.main()
