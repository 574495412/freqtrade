"""Fixed signals -> risk sizing -> real native entry, local synthetic tape only."""
import copy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'sources'))
import test_native_entries as ef
import test_native_account as af
import test_native_wallets as wf
from test_native_factors import resolved
from native_factor_entries import EntryInputs, EntryLiquidity, execute_fixed_entry, fixed_entry_signal, size_entry
from freqtrade.persistence import Trade
from rd1 import RiskError, StateError


class FixedEntryTests(unittest.TestCase):
    setUp=ef.NativeEntryTests.setUp
    tearDown=ef.NativeEntryTests.tearDown
    provider=af.NativeAccountTests.provider
    checkpoint=af.NativeAccountTests.checkpoint
    receipts=af.NativeAccountTests.receipts
    receipt=wf.NativeWalletTests.receipt
    boot=wf.NativeWalletTests.boot

    def bars(self,dates,closes):
        return pd.DataFrame(dict(date=dates,open=closes,high=np.asarray(closes)*1.01,
                                 low=np.asarray(closes)*.99,close=closes,volume=10000.))

    def prepare(self,market='spot',name='F1Momentum168Native',*,depth='100000'):
        life,gate,ex,bot,e,p,provider,start,guard,wallet=wf.NativeWalletTests.harness(self,market)
        now=ef.T.replace(hour=0,minute=5,second=0,microsecond=0)
        e=replace(e,source_id=af.SOURCE,snapshot=ef.snapshot(market,at=now,cursor=1))
        # Actual local CCXT fee calculation and explicit futures tier data.
        del ex.get_fee
        ex._leverage_tiers={p.pair:[dict(minNotional=0,maxNotional=50000,maxLeverage=1,maintenanceMarginRate=.004,maintAmt=0)]} if market=='futures' else {}
        universe=life.risk.plan['markets'][market]['pairs'];strategy=resolved(name,universe)
        day=pd.Timestamp(now).normalize()
        dates=pd.date_range(end=day-pd.Timedelta(hours=1),periods=169,freq='h')
        hours={pair:self.bars(dates,10*np.exp(np.arange(169)*(.002 if pair==p.pair else .001)+.01*np.sin(np.arange(169)))) for pair in universe}
        if name=='F1Momentum168Native': main=self.bars(pd.DatetimeIndex([day]),[10.])
        else:
            dates4=pd.date_range(end=day-pd.Timedelta(hours=4),periods=40,freq='4h')
            main=self.bars(dates4,np.r_[np.linspace(12,9,39),20.])
        inputs=EntryInputs(main,hours,'synthetic-fixed-bars')
        liquidity=EntryLiquidity(p.pair,'10','10',depth,'100000',now.replace(minute=0)-timedelta(hours=1),
            now,now,'synthetic-book','synthetic','synthetic-explicit-rules',now-timedelta(days=1),now+timedelta(days=1))
        raw=copy.deepcopy(ex._api.responses[0][0]);raw.update(transactTime=int(now.timestamp()*1000),updateTime=int(now.timestamp()*1000))
        ex._api.responses=[(raw,now)]
        return gate,ex,strategy,e,provider,wallet,inputs,liquidity,now

    def ready(self,ctx):
        gate,ex,strategy,e,provider,wallet,inputs,liquidity,now=ctx
        self.boot(provider,wallet,e)
        checked=wallet._checked_evidence
        return checked

    def response_for(self,ex,qty):
        raw,now=ex._api.responses[0];raw['origQty']=str(qty);ex._api.responses=[(raw,now)]

    def test_spot_fixed_f1_uses_computed_risk_stake_actual_wallet_and_native_callbacks(self):
        ctx=self.prepare();gate,ex,s,e,provider,wallet,b,l,now=ctx
        with wallet.clock.at(now):
            checked=self.ready(ctx)
            proposal,proof=size_entry(provider,checked,ex,wallet.wallets,l,D(1),now)
            self.response_for(ex,proposal.quantity)
            result=execute_fixed_entry(gate,ex,s,checked,b,l,'r1',now)
            trade=Trade.get_trades_proxy()[0]
            self.assertEqual(result['state'],'NATIVE_SUBMITTED_RECONCILIATION_PENDING')
            self.assertEqual(D(str(trade.stake_amount)),proposal.quantity*proposal.price)
            self.assertNotEqual(trade.stake_amount,50)
            self.assertEqual(trade.strategy,'F1Momentum168Native')
            self.assertEqual(trade.open_date_utc,now)
            self.assertEqual(trade.orders[0].amount,float(proposal.quantity))
            self.assertEqual(trade.fee_open,.0013)
            self.assertEqual(len(ex._api.requests),1)
            self.assertEqual(gate.s['requests']['r1']['native_binding']['trade_id'],trade.id)
            self.assertFalse(s.confirm_trade_entry())
            self.assertEqual(s.custom_stake_amount(),0)

    def test_futures_mark_sizes_risk_independently_of_entry_ask(self):
        ctx=self.prepare('futures');gate,ex,s,e,provider,wallet,b,l,now=ctx
        e=replace(e,snapshot=replace(e.snapshot,quotes=tuple(replace(q,price=D(12)) if q.pair==l.pair else q for q in e.snapshot.quotes)))
        ctx=(gate,ex,s,e,provider,wallet,b,l,now)
        with wallet.clock.at(now):
            checked=self.ready(ctx)
            proposal,proof=size_entry(provider,checked,ex,wallet.wallets,l,D(1),now)
            self.assertEqual(proof['valuation_price'],'12')
            self.assertLessEqual(proposal.quantity*D(12)*(D('.05')+2*provider.risk.cost),D(5))
            self.response_for(ex,proposal.quantity)
            execute_fixed_entry(gate,ex,s,checked,b,l,'r1',now)
            trade=Trade.get_trades_proxy()[0]
            self.assertEqual(trade.leverage,1)
            self.assertEqual(ex._api.requests[0]['api'],['fapiPrivate'])

    def test_f2_actual_ema_signal_scales_the_unscaled_budget_once(self):
        ctx=self.prepare(name='F2Volatility24Native');gate,ex,s,e,provider,wallet,b,l,now=ctx
        with wallet.clock.at(now):
            checked=self.ready(ctx)
            signal,proof=fixed_entry_signal(s,l.pair,b,now,provider.risk.plan['markets']['spot']['pairs'])
            self.assertIsNotNone(signal)
            self.assertLess(signal[1],1)
            unscaled,_=size_entry(provider,checked,ex,wallet.wallets,l,D(1),now)
            scaled,_=size_entry(provider,checked,ex,wallet.wallets,l,signal[1],now)
            self.assertLess(scaled.quantity,unscaled.quantity)
            self.assertLessEqual(scaled.quantity,unscaled.quantity*signal[1])
            self.response_for(ex,scaled.quantity)
            result=execute_fixed_entry(gate,ex,s,checked,b,l,'r1',now)
            self.assertEqual(result['sizing']['quantity'],str(scaled.quantity))

    def test_missing_cross_section_and_nonboundary_do_not_place_orders(self):
        ctx=self.prepare();gate,ex,s,e,provider,wallet,b,l,now=ctx
        hours=dict(b.hourly);hours.pop(next(p for p in hours if p!=l.pair))
        with wallet.clock.at(now):
            checked=self.ready(ctx)
            result=execute_fixed_entry(gate,ex,s,checked,replace(b,hourly=hours),l,'r1',now)
            self.assertEqual(result['state'],'SKIPPED')
            self.assertEqual(ex._api.requests,[])
            signal,proof=fixed_entry_signal(s,l.pair,b,now+timedelta(seconds=1),provider.risk.plan['markets']['spot']['pairs'])
            self.assertIsNone(signal)
            self.assertEqual(proof['reason'],'not_fixed_entry_boundary')

    def test_small_liquidity_budget_skips_instead_of_native_minimum_clamp(self):
        ctx=self.prepare(depth='55');gate,ex,s,e,provider,wallet,b,l,now=ctx
        with wallet.clock.at(now):
            checked=self.ready(ctx)
            result=execute_fixed_entry(gate,ex,s,checked,b,l,'r1',now)
            self.assertEqual(result['state'],'SKIPPED')
            self.assertEqual(result['sizing']['reason'],'minimum_order_skip_no_enlargement')
            self.assertEqual(ex._api.requests,[])

    def test_spread_stale_book_expired_rules_and_missing_futures_tiers_reject(self):
        ctx=self.prepare('futures');gate,ex,s,e,provider,wallet,b,l,now=ctx
        with wallet.clock.at(now):
            checked=self.ready(ctx)
            for bad in (replace(l,at=now-timedelta(seconds=16)),replace(l,rules_valid_until=now),
                        replace(l,volume_hour_open=l.volume_hour_open-timedelta(hours=1))):
                with self.assertRaises(RiskError):size_entry(provider,checked,ex,wallet.wallets,bad,D(1),now)
            proposal,proof=size_entry(provider,checked,ex,wallet.wallets,replace(l,bid='9.9'),D(1),now)
            self.assertIsNone(proposal);self.assertIn('spread',proof['reasons'])
            ex._leverage_tiers={}
            with self.assertRaisesRegex(RiskError,'futures leverage'):size_entry(provider,checked,ex,wallet.wallets,l,D(1),now)
            self.assertEqual(ex._api.requests,[])

    def test_native_wallet_callback_rejects_increased_stake_and_restores_after_failure(self):
        ctx=self.prepare();gate,ex,s,e,provider,wallet,b,l,now=ctx
        with wallet.clock.at(now):
            checked=self.ready(ctx)
            p,_=size_entry(provider,checked,ex,wallet.wallets,l,D(1),now)
            self.response_for(ex,p.quantity)
            original=wallet.wallets.validate_stake_amount
            with patch.object(wallet.wallets,'validate_stake_amount',side_effect=lambda **kw:original(**kw)+1):
                result=execute_fixed_entry(gate,ex,s,checked,b,l,'r1',now)
            self.assertEqual(result['state'],'NATIVE_DECLINED')
            self.assertEqual(ex._api.requests,[])
            self.assertFalse(s.confirm_trade_entry())
            with self.assertRaisesRegex(StateError,'already evaluated'):
                execute_fixed_entry(gate,ex,s,checked,b,l,'r1',now)
            with self.assertRaisesRegex(StateError,'another request identity'):
                execute_fixed_entry(gate,ex,s,checked,b,l,'new-id',now)

    def test_durable_decision_ack_failure_sends_no_request(self):
        ctx=self.prepare();gate,ex,s,e,provider,wallet,b,l,now=ctx
        with wallet.clock.at(now):
            checked=self.ready(ctx)
            save=gate.store.save
            def ack_lost(value):
                save(value);raise OSError('synthetic ack lost')
            with patch.object(gate.store,'save',side_effect=ack_lost):
                with self.assertRaises(StateError):execute_fixed_entry(gate,ex,s,checked,b,l,'r1',now)
            self.assertEqual(ex._api.requests,[])
            self.assertFalse(s.confirm_trade_entry())
            from native_entries import NativeEntryGate
            gate.close()
            reopened=NativeEntryGate(self.root/'spot-receipts',gate.lifecycle,after_fill_guard=gate.after_fill_guard)
            self.opened.append(reopened);wallet.attach(reopened);ex.gate=reopened
            with self.assertRaisesRegex(StateError,'already evaluated'):
                execute_fixed_entry(reopened,ex,s,checked,b,l,'r1',now)
            self.assertEqual(ex._api.requests,[])

    def test_f2_zero_volatility_and_future_hour_perturbation(self):
        ctx=self.prepare(name='F2Volatility24Native');gate,ex,s,e,provider,wallet,b,l,now=ctx
        universe=provider.risk.plan['markets']['spot']['pairs']
        with wallet.clock.at(now):
            first,proof=fixed_entry_signal(s,l.pair,b,now,universe)
            future=self.bars(pd.DatetimeIndex([pd.Timestamp(now).floor('h')]),[1000000.])
            hours=dict(b.hourly);hours[l.pair]=pd.concat([hours[l.pair],future],ignore_index=True)
            second,proof2=fixed_entry_signal(s,l.pair,replace(b,hourly=hours),now,universe)
            self.assertEqual(first,second);self.assertEqual(proof,proof2)
            hours[l.pair]=self.bars(b.hourly[l.pair].date,np.full(169,10.))
            missing,why=fixed_entry_signal(s,l.pair,replace(b,hourly=hours),now,universe)
            self.assertIsNone(missing);self.assertEqual(why['reason'],'missing_or_zero_volatility')

    def test_lost_native_reply_retains_request_and_restores_callbacks(self):
        import ccxt
        from freqtrade.exceptions import TemporaryError
        ctx=self.prepare();gate,ex,s,e,provider,wallet,b,l,now=ctx
        ex._api.responses=[ccxt.RequestTimeout('synthetic lost response')]
        with wallet.clock.at(now):
            checked=self.ready(ctx)
            with self.assertRaises(TemporaryError):execute_fixed_entry(gate,ex,s,checked,b,l,'r1',now)
            self.assertEqual(gate.s['requests']['r1']['state'],'SUBMISSION_UNKNOWN')
            self.assertEqual(gate.s['factor_decisions']['r1']['state'],'PREPARED')
            self.assertFalse(s.confirm_trade_entry())
            self.assertEqual(s.custom_stake_amount(),0)
            with self.assertRaisesRegex(StateError,'already evaluated'):
                execute_fixed_entry(gate,ex,s,checked,b,l,'r1',now)
            self.assertEqual(len(ex._api.requests),1)

    def test_immediate_fill_requires_explicit_fee_account_and_protection_reconciliation(self):
        ctx=self.prepare();gate,ex,s,e,provider,wallet,b,l,now=ctx
        with wallet.clock.at(now):
            checked=self.ready(ctx)
            p,_=size_entry(provider,checked,ex,wallet.wallets,l,D(1),now)
            raw,_=ex._api.responses[0]
            raw.update(status='FILLED',origQty=str(p.quantity),executedQty=str(p.quantity),
                       cummulativeQuoteQty=str(p.quantity*p.price),cumQuote=str(p.quantity*p.price),avgPrice=str(p.price))
            result=execute_fixed_entry(gate,ex,s,checked,b,l,'r1',now)
            self.assertEqual(result['state'],'NATIVE_SUBMITTED_RECONCILIATION_PENDING')
            trade=Trade.get_trades_proxy()[0]
            self.assertEqual(trade.amount,float(p.quantity))
            self.assertEqual(provider.ledger.last_sequence,0)
            with self.assertRaises(RiskError):gate.after_fill_guard.require_entry_clear()
            with self.assertRaises(RiskError):wallet.require_entry_clear(now)
            self.assertEqual(len(ex._api.requests),1)

    def test_noninteger_price_preserves_exact_approved_quantity_through_float_division(self):
        ctx=self.prepare(depth='60');gate,ex,s,e,provider,wallet,b,l,now=ctx
        l=replace(l,ask='10.01')
        with wallet.clock.at(now):
            checked=self.ready(ctx)
            p,_=size_entry(provider,checked,ex,wallet.wallets,l,D(1),now)
            self.assertEqual(p.quantity,D('.60'))
            self.assertNotEqual(float(p.quantity*p.price)/float(p.price),float(p.quantity))
            self.response_for(ex,p.quantity)
            ex._api.responses[0][0]['price']='10.01'
            result=execute_fixed_entry(gate,ex,s,checked,b,l,'r1',now)
            self.assertEqual(result['state'],'NATIVE_SUBMITTED_RECONCILIATION_PENDING')
            self.assertEqual(ex._api.requests[0]['params']['quantity'],'0.6')
            self.assertEqual(Trade.get_trades_proxy()[0].amount_requested,.6)

    def test_inactive_market_and_changed_signal_method_are_rejected(self):
        ctx=self.prepare();gate,ex,s,e,provider,wallet,b,l,now=ctx
        with wallet.clock.at(now):
            checked=self.ready(ctx)
            ex.markets[l.pair]['active']=False
            with self.assertRaisesRegex(RiskError,'active USDT'):size_entry(provider,checked,ex,wallet.wallets,l,D(1),now)
            with patch.object(s,'populate_entry_trend',lambda *a,**k: b.main):
                with self.assertRaisesRegex(RiskError,'fixed source'):
                    fixed_entry_signal(s,l.pair,b,now,provider.risk.plan['markets']['spot']['pairs'])
            self.assertEqual(ex._api.requests,[])


if __name__=='__main__':unittest.main()
