"""Source-ordered funding and native exits through one whole-account coordinator."""
import copy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'sources'))
import test_native_entries as ef
import test_native_wallets as wf
import test_native_account as af
import test_native_factor_runtime as mf
from native_factor_runtime import NativeFactorRuntime
from native_exits import NativeRiskExit, LocalExitExchange
from native_entries import NativeEntryGate, LocalBinanceTape
from native_wallets import WalletReceipt
from native_account import QuoteReceipt
from rd1 import Order, Funding, RiskError, StateError, digest, stamp
from freqtrade.persistence import Trade


class AccountRuntimeTests(unittest.TestCase):
    setUp=ef.NativeEntryTests.setUp
    tearDown=ef.NativeEntryTests.tearDown
    provider=wf.NativeWalletTests.provider
    checkpoint=wf.NativeWalletTests.checkpoint
    receipts=wf.NativeWalletTests.receipts
    event=wf.NativeWalletTests.event
    account=wf.NativeWalletTests.account
    execute=wf.NativeWalletTests.execute
    receipt=wf.NativeWalletTests.receipt
    boot=wf.NativeWalletTests.boot

    def harness(self,market='spot'):
        result=wf.NativeWalletTests.harness(self,market);self.entry_gate=result[1];return result

    def ready(self,market='futures'):
        wallet,provider,guard,trade,bot,e,checked,receipt,now=wf.NativeWalletTests.fill_cycle(self,market)
        exits=NativeRiskExit(self.root/'account-runtime-exits',self.entry_gate,wallet,create=True);self.opened.append(exits)
        return SimpleNamespace(gate=self.entry_gate,wallet=wallet,provider=provider,guard=guard,trade=trade,bot=bot,
            e=checked,now=now,runtime=NativeFactorRuntime(self.entry_gate,wallet),exits=exits,market=market)

    def wallet_receipt(self,e):
        snap=e.snapshot;reserved=sum((abs(o.remaining_quantity)*o.limit_price for o in snap.orders if o.increases_risk),D(0))
        used=reserved+(sum((p.collateral for p in snap.positions),D(0)) if e.market=='futures' else 0)
        balances={'USDT':dict(free=str(snap.balance-used),used=str(used),total=str(snap.balance))};positions=[]
        quotes={q.pair:q.price for q in snap.quotes}
        for p in snap.positions:
            if e.market=='spot':
                locked=sum((abs(o.remaining_quantity) for o in snap.orders if o.pair==p.pair and not o.increases_risk),D(0))
                balances[p.pair.split('/')[0]]=dict(free=str(p.quantity-locked),used=str(locked),total=str(p.quantity))
            else:positions.append(dict(symbol=p.pair,side='long',contracts=str(p.quantity),leverage='1',initialMargin=str(p.collateral),
                unrealizedPnl=str(p.quantity*(quotes[p.pair]-p.entry_price)),entryPrice=str(p.entry_price)))
        return WalletReceipt(e.account_id,e.market,'synthetic-runtime-wallet','synthetic',snap.at,snap.balance_at,snap.orders_at,
                             snap.balance_semantics,balances,tuple(positions),True,True)

    def evidence(self,c,now,cursor,*,cash,quantity='5',mark='10',funding=(),open_exit=None):
        qty=D(quantity);positions=tuple(replace(p,quantity=qty,collateral=qty*10 if c.market=='futures' else D(0)) for p in c.e.snapshot.positions) if qty else ()
        orders=();bindings={};envelopes=()
        if open_exit:
            report=c.exits.envelope(open_exit,now);raw=json.loads(report.payload_json)
            sid='exit-source-'+raw['id'];created=c.exits.s['actions'][open_exit]['claimed_at']
            from rd1 import parse
            orders=(Order(sid,c.trade.pair,-D(str(raw['remaining'])),D(str(raw['price'])),parse(created),False,True),)
            bindings={sid:(c.trade.pair,raw['id'])};envelopes=(report,)
        snap=replace(c.e.snapshot,at=now,balance_at=now,orders_at=now,cursor=cursor,balance=D(cash),positions=positions,orders=orders,
                     funding=tuple(funding),quotes=tuple(replace(q,at=now,price=D(mark) if q.pair==c.trade.pair else q.price) for q in c.e.snapshot.quotes))
        return replace(c.e,snapshot=snap,position_bindings=c.e.position_bindings if qty else {},order_bindings=bindings,
                       order_envelopes=envelopes,partial_base_fees={})

    def financial(self,c,sequence,now,kind,**values):
        event=dict(event_id='runtime-event-'+str(sequence),sequence=sequence,event_at=stamp(now),received_at=stamp(now),kind=kind,
            source_id=af.SOURCE,evidence_class='synthetic',account_id=c.e.account_id,market=c.market,pair=c.trade.pair,
            campaign_id='campaign-'+str(c.trade.id),native_trade_id=c.trade.id)
        event.update(values);return event

    def observe(self,c,label,e,events=(),actions=()):
        now=e.snapshot.at
        return c.runtime.observe_account(label,e,self.wallet_receipt(e),self.receipts(e),{},events,c.bot,now,
            complete_sequence=max([c.provider.ledger.last_sequence]+[x['sequence'] for x in events]),
            complete_from_inception=True,funding_complete=True,exit_controller=c.exits,exit_actions=actions)

    def funding_cycle(self):
        c=self.ready();now=c.now+timedelta(seconds=1)
        event=self.financial(c,2,now,'funding_accrual',funding_id='fund-1',cost_quote='2')
        e=self.evidence(c,now,3,cash='999.95',funding=(Funding('fund-1',D(2),False),))
        with c.wallet.clock.at(now):a=self.observe(c,'accrued',e,[event])
        self.assertEqual(c.trade.funding_fees,-2);self.assertEqual(c.trade.funding_fee_running,-2)
        later=now+timedelta(seconds=1)
        event=self.financial(c,3,later,'funding_settlement',funding_id='fund-1',cost_quote='2')
        e=self.evidence(c,later,4,cash='997.95',funding=(Funding('fund-1',D(2),True),))
        with c.wallet.clock.at(later):b=self.observe(c,'settled',e,[event])
        self.assertEqual(a['decision'].equity,b['decision'].equity)
        self.assertEqual(c.trade.funding_fees,-2)
        self.assertEqual(b['audit']['pending_funding_cost'],'0')
        self.assertEqual(b['audit']['settled_funding_cash'],'-2')
        return c,e,later

    def dispatch(self,c,e,now,*,venue=200):
        action=next(a for a in c.provider.risk.evaluate(e.snapshot,now).actions if a['kind']=='EXIT_POSITION' and a['target']=='campaign-'+str(c.trade.id))
        aid=action['id'];old=c.exits.s['actions'].get(aid);attempt=1 if old is None else old['attempt']+1
        key=dict(account=c.provider.risk.s['identity'],action=aid)
        if attempt>1:key['attempt']=attempt
        base=c.trade.pair.split('/')[0]
        raw=dict(symbol=base+'USDT',orderId=venue,clientOrderId='rd1x-'+digest(key)[:28],time=int(now.timestamp()*1000),
                 transactTime=int(now.timestamp()*1000),updateTime=int(now.timestamp()*1000),price='5',origQty=str(c.trade.amount),executedQty='0',
                 cummulativeQuoteQty='0',cumQuote='0',avgPrice='0',status='NEW',timeInForce='GTC',type='LIMIT',side='SELL',reduceOnly=True)
        market=ef.market_fixture(c.market);market.update(id=base+'USDT',symbol=c.trade.pair,base=base,baseId=base)
        tape=LocalBinanceTape([market],[(raw,now)],allowed_methods=('POST','GET'))
        ex=LocalExitExchange(tape,c.exits);self.opened.append(ex)
        self.assertEqual(c.exits.dispatch(c.trade,e,ex,c.bot,now,limit='5'),aid)
        return aid,ex,raw

    def terminal(self,c,aid,ex,raw,now,quantity,status='FILLED'):
        raw=copy.deepcopy(raw);raw.update(status=status,executedQty=str(quantity),cummulativeQuoteQty=str(D(quantity)*5),
             cumQuote=str(D(quantity)*5),avgPrice='5',updateTime=int(now.timestamp()*1000))
        ex._api.responses=[(raw,now)];ex.query(aid,now)
        return self.financial(c,c.provider.ledger.last_sequence+1,now,'fill',order_id=str(raw['orderId']),fill_id='fill-'+str(raw['orderId']),
                              signed_quantity=str(-D(quantity)),price='5',fee_quote=str(D(quantity)*D('.005')),fee_base='0')

    def test_accrual_then_settlement_changes_cash_once_and_preserves_nav(self):self.funding_cycle()

    def test_funding_partial_exit_new_credit_and_final_exit_preserve_full_economics(self):
        c,e,now=self.funding_cycle();now+=timedelta(seconds=1);funding=e.snapshot.funding
        e=self.evidence(c,now,5,cash='997.95',mark='5',funding=funding)
        with c.wallet.clock.at(now):
            checked=self.observe(c,'drawdown',e)['evidence'];aid,ex,raw=self.dispatch(c,checked,now)
            opened=self.evidence(c,now,6,cash='997.95',mark='5',funding=funding,open_exit=aid)
            self.observe(c,'exit-open',opened,actions=[aid])
        now+=timedelta(seconds=1)
        with c.wallet.clock.at(now):
            event=self.terminal(c,aid,ex,raw,now,'2','CANCELED')
            e=self.evidence(c,now,7,cash='987.94',quantity='3',mark='5',funding=funding)
            partial=self.observe(c,'partial-exit',e,[event],[aid])
        self.assertEqual(c.trade.amount,3);self.assertEqual(c.trade.funding_fees,-2)
        self.assertEqual(c.trade.orders[-1].funding_fee,-2)
        self.assertEqual(D(partial['decision'].equity),D('972.9280'))  # frozen futures base exit cost is 8 bps
        now+=timedelta(seconds=1);funding=(*funding,Funding('credit-2',D('-.5'),True))
        event=self.financial(c,5,now,'funding_settlement',funding_id='credit-2',cost_quote='-.5')
        e=self.evidence(c,now,8,cash='988.44',quantity='3',mark='5',funding=funding)
        with c.wallet.clock.at(now):
            checked=self.observe(c,'new-credit',e,[event])['evidence']
            self.assertEqual(c.trade.funding_fees,-1.5);self.assertEqual(c.trade.funding_fee_running,.5)
            aid,ex,raw=self.dispatch(c,checked,now,venue=201)
        now+=timedelta(seconds=1)
        with c.wallet.clock.at(now):
            event=self.terminal(c,aid,ex,raw,now,'3')
            e=self.evidence(c,now,9,cash='973.425',quantity='0',mark='5',funding=funding)
            final=self.observe(c,'final-exit',e,[event],[aid])
            c.guard.require_entry_clear();c.wallet.require_entry_clear(now)
        self.assertFalse(c.trade.is_open);self.assertEqual(c.trade.funding_fees,-1.5)
        self.assertEqual([o.funding_fee for o in c.trade.orders],[0,-2,.5])
        self.assertEqual(D(final['decision'].equity),D('973.425'))
        self.assertEqual(D(final['audit']['fees_paid']),D('.075'))
        self.assertEqual(D(final['audit']['settled_funding_cash']),D('-1.5'))
        self.assertEqual(D(final['audit']['realized_price_pnl']),D('-25'))

    def test_funding_native_commit_ack_loss_resumes_without_double_charge(self):
        c=self.ready();now=c.now+timedelta(seconds=1)
        event=self.financial(c,2,now,'funding_settlement',funding_id='fund-1',cost_quote='2')
        e=self.evidence(c,now,3,cash='997.95',funding=(Funding('fund-1',D(2),True),))
        with c.wallet.clock.at(now):
            commit=Trade.commit
            def ack_lost():commit();raise OSError('synthetic native funding commit acknowledgement lost')
            with patch.object(Trade,'commit',side_effect=ack_lost):
                with self.assertRaises(OSError):self.observe(c,'recover-fund',e,[event])
            self.assertEqual(c.trade.funding_fees,-2)
            with self.assertRaisesRegex(RiskError,'pending whole-account'):c.gate.require_account_batch_clear()
            c.exits.close();c.gate.close()
            gate=NativeEntryGate(self.root/'futures-receipts',c.gate.lifecycle,after_fill_guard=c.guard);self.opened.append(gate)
            c.wallet.attach(gate);c.gate=gate;c.runtime=NativeFactorRuntime(gate,c.wallet)
            c.exits=NativeRiskExit(self.root/'account-runtime-exits',gate,c.wallet);self.opened.append(c.exits)
            result=self.observe(c,'recover-fund',e,[event])
            self.assertEqual(c.provider.ledger.last_sequence,2)
            self.assertEqual(D(result['audit']['settled_funding_cash']),D(-2))
            self.assertEqual(c.trade.funding_fees,-2)

    def test_wrong_native_funding_is_not_overwritten_by_source_batch(self):
        c=self.ready();now=c.now+timedelta(seconds=1)
        # Even an unacknowledged mutation equal to the coming target must not be
        # treated as recovery without a durably recorded funding intent.
        c.trade.set_funding_fees(-2);Trade.commit()
        event=self.financial(c,2,now,'funding_settlement',funding_id='fund-1',cost_quote='2')
        e=self.evidence(c,now,3,cash='997.95',funding=(Funding('fund-1',D(2),True),))
        with c.wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError,'outside acknowledged'):self.observe(c,'wrong-native',e,[event])
            self.assertEqual(c.trade.funding_fees,-2)
            with self.assertRaises(RiskError):c.wallet.require_entry_clear(now)

    def test_incomplete_exit_inventory_and_spot_funding_revoke_old_permission(self):
        c=self.ready('spot');now=c.now+timedelta(seconds=1)
        e=self.evidence(c,now,3,cash='949.95',mark='5')
        with c.wallet.clock.at(now):
            checked=self.observe(c,'drawdown',e)['evidence'];aid,ex,raw=self.dispatch(c,checked,now)
            opened=self.evidence(c,now,4,cash='949.95',mark='5',open_exit=aid)
            with self.assertRaisesRegex(RiskError,'every unresolved native exit'):
                self.observe(c,'missing-exit',opened)
            with self.assertRaises(RiskError):c.wallet.require_entry_clear(now)
            event=self.financial(c,2,now,'funding_settlement',funding_id='invalid-spot',cost_quote='1')
            with self.assertRaisesRegex(RiskError,'Funding must be futures'):
                self.observe(c,'spot-funding',opened,[event],[aid])
            self.assertEqual(c.provider.ledger.last_sequence,1)

    def test_same_batch_funding_and_full_exit_recover_then_settle_after_close(self):
        c=self.ready();now=c.now+timedelta(seconds=1)
        e=self.evidence(c,now,3,cash='999.95',mark='5')
        with c.wallet.clock.at(now):
            checked=self.observe(c,'drawdown',e)['evidence'];aid,ex,raw=self.dispatch(c,checked,now)
        now+=timedelta(seconds=1)
        with c.wallet.clock.at(now):
            fill=self.terminal(c,aid,ex,raw,now,'5');fill.update(sequence=3,event_id='runtime-event-3')
            funding=self.financial(c,2,now,'funding_accrual',funding_id='pending-on-close',cost_quote='2')
            e=self.evidence(c,now,4,cash='974.925',quantity='0',mark='5',funding=(Funding('pending-on-close',D(2),False),))
            with patch.object(c.provider,'checkpoint',side_effect=OSError('synthetic account check failure after close')):
                with self.assertRaises(OSError):self.observe(c,'close-with-fund',e,[funding,fill],[aid])
            self.assertFalse(c.trade.is_open);self.assertEqual(c.trade.funding_fees,-2)
            with patch.object(c.bot.strategy,'order_filled',side_effect=AssertionError('closed fill must not repeat')):
                result=self.observe(c,'close-with-fund',e,[funding,fill],[aid])
            self.assertEqual(D(result['decision'].equity),D('972.925'))
        later=now+timedelta(seconds=1)
        event=self.financial(c,4,later,'funding_settlement',funding_id='pending-on-close',cost_quote='2')
        e=self.evidence(c,later,5,cash='972.925',quantity='0',mark='5',funding=(Funding('pending-on-close',D(2),True),))
        with c.wallet.clock.at(later):
            settled=self.observe(c,'closed-settlement',e,[event]);c.guard.require_entry_clear()
        self.assertEqual(result['decision'].equity,settled['decision'].equity)
        self.assertEqual(c.trade.funding_fees,-2);self.assertEqual(c.trade.funding_fee_running,0)

    def test_funding_after_same_batch_exit_needs_explicit_intermediate_evidence(self):
        c=self.ready();now=c.now+timedelta(seconds=1)
        e=self.evidence(c,now,3,cash='999.95',mark='5')
        with c.wallet.clock.at(now):
            checked=self.observe(c,'drawdown',e)['evidence'];aid,ex,raw=self.dispatch(c,checked,now)
        now+=timedelta(seconds=1)
        with c.wallet.clock.at(now):
            fill=self.terminal(c,aid,ex,raw,now,'5')
            late=self.financial(c,3,now,'funding_settlement',funding_id='late-fund',cost_quote='2')
            e=self.evidence(c,now,4,cash='972.925',quantity='0',mark='5',funding=(Funding('late-fund',D(2),True),))
            with self.assertRaisesRegex(RiskError,'precede same-batch exit'):
                self.observe(c,'unsupported-order',e,[fill,late],[aid])
            self.assertEqual(c.provider.ledger.last_sequence,1)
            self.assertTrue(c.trade.is_open);self.assertEqual(c.trade.amount,5)
            with self.assertRaises(RiskError):c.wallet.require_entry_clear(now)


class MultiAccountRuntimeTests(unittest.TestCase):
    setUp=mf.FactorRuntimeTests.setUp
    tearDown=mf.FactorRuntimeTests.tearDown
    provider=mf.FactorRuntimeTests.provider
    checkpoint=mf.FactorRuntimeTests.checkpoint
    receipts=mf.FactorRuntimeTests.receipts
    bars=mf.FactorRuntimeTests.bars
    setup_runtime=mf.FactorRuntimeTests.setup_runtime
    account=mf.FactorRuntimeTests.account
    prepare_response=mf.FactorRuntimeTests.prepare_response
    reports=mf.FactorRuntimeTests.reports
    observe=mf.FactorRuntimeTests.observe
    submit_open=mf.FactorRuntimeTests.submit_open
    fill_events=mf.FactorRuntimeTests.fill_events
    cycle=mf.FactorRuntimeTests.cycle
    wallet_receipt=AccountRuntimeTests.wallet_receipt
    financial=AccountRuntimeTests.financial
    dispatch=AccountRuntimeTests.dispatch
    terminal=AccountRuntimeTests.terminal

    def bind_exits(self,c):
        c.exits=NativeRiskExit(self.root/'multi-account-exits',c.gate,c.wallet,create=True);self.opened.append(c.exits)

    def general(self,c,label,e,events=(),actions=(),reports=None):
        return c.runtime.observe_account(label,e,self.wallet_receipt(e),self.receipts(e),reports or {},events,c.bot,e.snapshot.at,
            complete_sequence=max([c.provider.ledger.last_sequence]+[x['sequence'] for x in events]),
            complete_from_inception=True,funding_complete=True,exit_controller=c.exits,exit_actions=actions)

    def test_three_factor_campaigns_funding_and_one_exit_preserve_other_two_holdings(self):
        c=self.cycle('futures');self.bind_exits(c);c.trade=Trade.get_trades_proxy()[0];c.e=c.wallet._checked_evidence
        other=Trade.get_trades_proxy()[1];now=c.now+timedelta(seconds=2)
        debit=self.financial(c,4,now,'funding_settlement',funding_id='btc-debit',cost_quote='2')
        credit=self.financial(c,5,now,'funding_settlement',funding_id='eth-credit',cost_quote='-.5')
        credit.update(pair=other.pair,campaign_id='campaign-'+str(other.id),native_trade_id=other.id)
        base=c.e;before=D(c.provider.risk.equity(base.snapshot))
        fund=(Funding('btc-debit',D(2),True),Funding('eth-credit',D('-.5'),True))
        e=replace(base,snapshot=replace(base.snapshot,at=now,balance_at=now,orders_at=now,cursor=6,balance=base.snapshot.balance-D('1.5'),
            funding=fund,quotes=tuple(replace(q,at=now) for q in base.snapshot.quotes)))
        with c.wallet.clock.at(now):
            result=self.general(c,'multi-funding',e,[debit,credit])
        self.assertEqual(D(result['decision'].equity),before-D('1.5'))
        self.assertEqual([t.funding_fees for t in Trade.get_trades_proxy()],[-2,.5,0])
        now+=timedelta(seconds=1)
        e=replace(e,snapshot=replace(e.snapshot,at=now,balance_at=now,orders_at=now,cursor=7,
            quotes=tuple(replace(q,at=now,price=D(5) if q.pair==c.trade.pair else q.price) for q in e.snapshot.quotes)))
        with c.wallet.clock.at(now):
            checked=self.general(c,'first-drawdown',e)['evidence'];aid,ex,raw=self.dispatch(c,checked,now)
        now+=timedelta(seconds=1);quantity=D(str(c.trade.amount))
        with c.wallet.clock.at(now):
            event=self.terminal(c,aid,ex,raw,now,str(quantity))
            e=replace(e,position_bindings={k:v for k,v in e.position_bindings.items() if v!=c.trade.id},
                snapshot=replace(e.snapshot,at=now,balance_at=now,orders_at=now,cursor=8,balance=e.snapshot.balance-quantity*D('5.005'),
                    positions=tuple(p for p in e.snapshot.positions if p.pair!=c.trade.pair),quotes=tuple(replace(q,at=now) for q in e.snapshot.quotes)))
            result=self.general(c,'first-exited',e,[event],[aid]);c.guard.require_entry_clear()
        self.assertEqual(len(result['evidence'].snapshot.positions),2)
        self.assertEqual(sum(p.quantity for p in result['evidence'].snapshot.positions),D('19.36'))
        self.assertEqual(D(result['decision'].equity),D('949.60632'))
        self.assertEqual(D(result['audit']['fees_paid']),D('.3388'))
        self.assertEqual([t.is_open for t in Trade.get_trades_proxy()],[False,True,True])
        self.assertEqual([t.funding_fees for t in Trade.get_trades_proxy()],[-2,.5,0])

    def test_terminal_exit_reconciles_while_another_entry_remains_partial(self):
        c=self.submit_open(self.setup_runtime('futures'),count=2);self.bind_exits(c)
        now=c.now+timedelta(seconds=1);reports=self.reports(c,now,fill=100);rid=next(iter(reports))
        raw=json.loads(reports[rid].payload_json);raw.update(status='open',filled=3.,remaining=raw['amount']-3,average=10.,cost=30.)
        reports[rid]=replace(reports[rid],payload_json=json.dumps(raw));events=self.fill_events(c,reports,now)
        with c.wallet.clock.at(now):result=self.observe(c,'mixed-initial',reports,now,4,events)
        c.trade=Trade.get_trades_proxy()[1];c.e=result['evidence'];now+=timedelta(seconds=1)
        report=replace(reports[rid],received_at=now)
        e=replace(c.e,order_envelopes=(report,),snapshot=replace(c.e.snapshot,at=now,balance_at=now,orders_at=now,cursor=5,
            quotes=tuple(replace(q,at=now,price=D(5) if q.pair==c.trade.pair else q.price) for q in c.e.snapshot.quotes)))
        with c.wallet.clock.at(now):
            checked=self.general(c,'mixed-drawdown',e,reports={rid:report})['evidence'];aid,ex,venue=self.dispatch(c,checked,now)
        now+=timedelta(seconds=1)
        with c.wallet.clock.at(now):
            event=self.terminal(c,aid,ex,venue,now,str(c.trade.amount));report=replace(report,received_at=now)
            e=replace(e,order_envelopes=(report,),position_bindings={k:v for k,v in e.position_bindings.items() if v!=c.trade.id},
                snapshot=replace(e.snapshot,at=now,balance_at=now,orders_at=now,cursor=6,balance=D('951.4248'),
                    positions=tuple(p for p in e.snapshot.positions if p.pair!=c.trade.pair),quotes=tuple(replace(q,at=now) for q in e.snapshot.quotes)))
            result=self.general(c,'exit-with-partial',e,[event],[aid],{rid:report})
            self.assertEqual(c.exits.s['actions'][aid]['state'],'TERMINAL_RECONCILED')
            self.assertEqual(D(result['decision'].equity),D('951.4008'))
            self.assertIsNone(c.gate.s['pending_account_batch'])
            with self.assertRaises(RiskError):c.guard.require_entry_clear()


if __name__=='__main__':unittest.main()
