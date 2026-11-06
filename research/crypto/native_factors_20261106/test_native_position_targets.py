"""Fixed daily factor targets to native partial orders and full account arithmetic."""
import copy
import itertools
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'sources'))
from native_position_targets import PositionReductionInputs,build_position_targets
from native_strategy_exits import ExitBarInputs,ExitRate
from native_exits import NativeRiskExit,LocalExitExchange
from native_entries import LocalBinanceTape
from native_evidence import decimal_text
from rd1 import RiskError,StateError,digest,Position,Quote,Correlations
from freqtrade.persistence import Trade
from test_native_factors import resolved
from test_native_strategy_exits import bars
import test_native_entries as ef
import test_native_account as af
import test_native_wallets as wf
import test_rd1 as rf


class NativePositionTargetTests(unittest.TestCase):
    tearDown=ef.NativeEntryTests.tearDown
    provider=wf.NativeWalletTests.provider
    checkpoint=wf.NativeWalletTests.checkpoint
    receipts=wf.NativeWalletTests.receipts
    event=wf.NativeWalletTests.event
    account=wf.NativeWalletTests.account
    execute=wf.NativeWalletTests.execute
    receipt=wf.NativeWalletTests.receipt
    boot=wf.NativeWalletTests.boot
    fill_cycle=wf.NativeWalletTests.fill_cycle

    def setUp(self):
        ef.NativeEntryTests.setUp(self)
        self.start=ef.T.replace(hour=0,minute=4,second=58)
        for module in (ef,af,wf,rf):self.stack.enter_context(patch.object(module,'T',self.start))
        original_snapshot=ef.snapshot
        def snapshot(market='spot',**kwargs):
            kwargs.setdefault('at',self.start)
            return original_snapshot(market,**kwargs)
        self.stack.enter_context(patch.object(ef,'snapshot',snapshot))

    def harness(self,market='spot'):
        result=wf.NativeWalletTests.harness(self,market);self.gate=result[1];return result

    def position(self,market='spot',name='F2Volatility24Native',price='10'):
        wallet,p,guard,trade,bot,e,_,_,now=self.fill_cycle(market)
        bot.strategy=resolved(name,(trade.pair,))
        now+=timedelta(seconds=1)
        e=replace(e,snapshot=replace(e.snapshot,at=now,balance_at=now,orders_at=now,cursor=3,
            quotes=tuple(replace(q,at=now,price=D(price) if q.pair==trade.pair else q.price) for q in e.snapshot.quotes)))
        with wallet.clock.at(now):
            wallet.stage('valuation',self.receipt(e,quantity='5',cash_used='50' if market=='futures' else '0',collateral='50',upnl=str(D(5)*(D(price)-10))),e,now)
            e,_=self.checkpoint(p,e);wallet.confirm(e,now)
        c=NativeRiskExit(self.root/'position-exits',self.gate,wallet,create=True);self.opened.append(c)
        end=pd.Timestamp(now).normalize()
        values=10*np.exp(np.cumsum(np.array([.01,-.01]*13)[:25]))
        hours=bars(end,60,values)
        reductions=PositionReductionInputs({trade.pair:hours},'synthetic-hourly-source')
        rate=ExitRate(trade.pair,price,now,now,'synthetic-exit-bid','synthetic') if market=='futures' else None
        signals=ExitBarInputs(None,{},'synthetic-no-primary-bars',exit_rate=rate)
        return c,wallet,p,guard,trade,bot,e,now,reductions,signals

    def exchange(self,c,trade,now,quantity,price='10',ordinal=1):
        aid=digest({'account':c.risk.s['identity'],'trade_id':trade.id,'kind':'POSITION_REDUCTION'})[:24]
        key={'account':c.risk.s['identity'],'action':aid}
        if ordinal>1:key['attempt']=ordinal
        client='rd1x-'+digest(key)[:28]
        raw={'symbol':'BTCUSDT','orderId':200+ordinal-1,'clientOrderId':client,'time':int(now.timestamp()*1000),'transactTime':int(now.timestamp()*1000),
            'updateTime':int(now.timestamp()*1000),'price':price,'origQty':str(quantity),'executedQty':'0','cummulativeQuoteQty':'0','cumQuote':'0','avgPrice':'0',
            'status':'NEW','timeInForce':'GTC','type':'LIMIT','side':'SELL','reduceOnly':True}
        tape=LocalBinanceTape([ef.market_fixture(c.risk.market)],[(raw,now)],allowed_methods=('POST','GET'))
        ex=LocalExitExchange(tape,c);self.opened.append(ex)
        return ex,raw

    def planned(self,market='spot'):
        c,wallet,p,guard,trade,bot,e,now,ri,si=self.position(market)
        with wallet.clock.at(now):
            targets,daily,proof=build_position_targets(p,e,bot.strategy,ri,now,{})
        # Inspect official precision without using a test-provided target to dispatch.
        ex,raw=self.exchange(c,trade,now,'1')
        target=D(decimal_text(ex.amount_to_contract_precision(trade.pair,float(targets[trade.pair]))))
        quantity=D(5)-target;raw['origQty']=str(quantity)
        with wallet.clock.at(now):aid=c.dispatch(trade,e,ex,bot,now,limit='10',strategy_inputs=si,reduction_inputs=ri)
        self.assertIsNotNone(aid)
        self.assertEqual(c.s['actions'][aid]['target_remaining_quantity'],str(target))
        self.assertEqual(D(ex._api.requests[0]['params']['quantity']),quantity)
        self.assertEqual(c.s['actions'][aid]['exit_type'],'partial_exit')
        self.assertEqual(trade.exit_reason,'F2_daily_scale')
        self.assertEqual(len(c.s['daily_position_targets']),1)
        return c,wallet,p,guard,trade,bot,e,now,ri,si,ex,raw,aid,target,quantity

    def fill_reduction(self,values,*,filled=None,status='FILLED'):
        c,wallet,p,guard,trade,bot,_,now,ri,si,ex,raw,aid,target,quantity=values
        now+=timedelta(seconds=1);filled=quantity if filled is None else D(filled)
        price=D(raw['price'])
        terminal=copy.deepcopy(raw);terminal.update(status=status,executedQty=str(filled),cummulativeQuoteQty=str(filled*price),cumQuote=str(filled*price),avgPrice=str(price),updateTime=int(now.timestamp()*1000))
        ex._api.responses=[(terminal,now)]
        with wallet.clock.at(now):ex.query(aid,now)
        fee=filled*price*D('.001');p.append(self.event(trade,2,quantity=str(-filled),fee=str(fee),seconds=3,order_id='200',price=str(price)))
        remaining=D(5)-filled
        cash=D('949.95')+filled*price-fee if c.risk.market=='spot' else D('999.95')+filled*(price-10)-fee
        e=self.account(c.envelope(aid,now),trade,str(remaining),str(cash),cursor=4,market=c.risk.market)
        e=replace(e,snapshot=replace(e.snapshot,quotes=tuple(replace(q,price=price) if q.pair==trade.pair else q for q in e.snapshot.quotes)))
        if c.risk.market=='futures':e=replace(e,snapshot=replace(e.snapshot,positions=tuple(replace(pos,collateral=remaining*10) for pos in e.snapshot.positions)))
        with wallet.clock.at(now):
            bot.wallets=wallet.stage('reduction-filled',self.receipt(e,quantity=str(remaining),cash_used=str(remaining*10) if c.risk.market=='futures' else '0',collateral=str(remaining*10),upnl=str(remaining*(price-10))),e,now)
            c.apply_filled(aid,bot,now)
            checked,audit=self.checkpoint(p,e);wallet.confirm(checked,now)
            result=c.reconcile(aid,checked,now)
        self.assertTrue(trade.is_open)
        self.assertEqual(D(str(trade.amount)),remaining)
        self.assertEqual(D(audit['fees_paid']),D('.05')+fee)
        self.assertEqual(D(audit['observed_equity']),D(1000)+D(5)*(price-10)-D('.05')-fee-remaining*price*p.exit_rate)
        return checked,audit,result,e,now

    def test_spot_fixed_f2_budget_dispatches_and_reconciles_partial_native_exit(self):
        values=self.planned();checked,_,result,e,now=self.fill_reduction(values)
        c,wallet,p,guard,trade,bot,_,_,ri,si,ex,_,_,target,_=values
        self.assertFalse(result['requires_further_exit'])
        self.assertEqual(D(str(trade.amount)),target)
        # A later poll uses the persisted daily cap, not holding * scale again.
        now+=timedelta(seconds=1)
        e=replace(e,snapshot=replace(e.snapshot,at=now,balance_at=now,orders_at=now,cursor=5,quotes=tuple(replace(q,at=now) for q in e.snapshot.quotes)))
        with wallet.clock.at(now):
            wallet.stage('later-poll',self.receipt(e,quantity=str(target)),e,now)
            checked,_=self.checkpoint(p,e);wallet.confirm(checked,now)
            self.assertIsNone(c.dispatch(trade,checked,ex,bot,now,limit='10',strategy_inputs=si,reduction_inputs=ri))
        self.assertEqual(len(ex._api.requests),2)
        self.assertEqual(len(c.s['daily_position_targets']),1)

    def test_futures_scaled_reduction_preserves_collateral_and_native_reduce_only(self):
        values=self.planned('futures');_,_,result,_,_=self.fill_reduction(values)
        self.assertFalse(result['requires_further_exit'])
        self.assertTrue(values[10]._api.requests[0]['params']['reduceOnly'])

    def test_missing_daily_decision_is_not_backfilled_at_a_later_poll(self):
        c,wallet,p,_,trade,bot,e,now,ri,si=self.position()
        with wallet.clock.at(now):
            targets,daily,proof=build_position_targets(p,e,bot.strategy,ri,now+timedelta(seconds=1),{})
        self.assertEqual(daily,{})
        self.assertEqual(targets[trade.pair],D(5))
        self.assertEqual(proof['factor_status'][trade.pair],'no_0005_checkpoint_no_backfilled_target')

    def test_missing_volatility_does_not_invent_a_zero_target(self):
        c,wallet,p,_,trade,bot,e,now,ri,si=self.position()
        with wallet.clock.at(now):
            targets,daily,proof=build_position_targets(p,e,bot.strategy,replace(ri,hourly={}),now,{})
        self.assertEqual(targets[trade.pair],D(5));self.assertEqual(daily,{})
        self.assertEqual(proof['factor_status'][trade.pair],'missing_or_zero_vol_no_new_factor_target')

    def test_partial_cancel_reconciles_against_target_not_against_flat(self):
        values=self.planned();_,_,result,e,now=self.fill_reduction(values,filled='1',status='CANCELED')
        self.assertTrue(result['requires_further_exit'])
        self.assertEqual(D(result['remaining_quantity']),D(4))
        self.assertGreater(D(result['remaining_quantity']),values[13])
        c,wallet,p,_,trade,bot,_,_,ri,si,_,_,_,target,_=values
        now+=timedelta(seconds=1)
        e=replace(e,snapshot=replace(e.snapshot,at=now,balance_at=now,orders_at=now,cursor=5,quotes=tuple(replace(q,at=now) for q in e.snapshot.quotes)))
        ex,_=self.exchange(c,trade,now,D(4)-target,ordinal=2)
        with wallet.clock.at(now):
            wallet.stage('remaining-retry',self.receipt(e,quantity='4'),e,now)
            checked,_=self.checkpoint(p,e);wallet.confirm(checked,now)
            aid=c.dispatch(trade,checked,ex,bot,now,limit='10',strategy_inputs=si,reduction_inputs=ri)
        self.assertEqual(c.s['actions'][aid]['attempt'],2)
        self.assertEqual(D(ex._api.requests[0]['params']['quantity']),D(4)-target)

    def test_native_minimum_blocks_tiny_reduction_without_enlargement(self):
        c,wallet,p,_,trade,bot,e,now,ri,si=self.position()
        # A smaller synthetic volatility gives a cap close to, but below, 5 units.
        ri=replace(ri,hourly={trade.pair:bars(pd.Timestamp(now).normalize(),60,10*np.exp(np.cumsum(np.array([.0042,-.0042]*13)[:25])))})
        ex,_=self.exchange(c,trade,now,'1')
        with wallet.clock.at(now):result=c.dispatch(trade,e,ex,bot,now,limit='10',strategy_inputs=si,reduction_inputs=ri)
        self.assertIsNone(result)
        self.assertEqual(next(iter(c.s['reduction_evaluations'].values()))['state'],'BLOCKED_NATIVE_MINIMUM')
        self.assertEqual(ex._api.requests,[])
        self.assertEqual(c.s['actions'],{})

    def test_existing_unknown_reduction_blocks_other_exit_sources(self):
        values=self.planned();c,wallet,_,_,trade,bot,e,now,ri,si,ex,*_=values
        with wallet.clock.at(now):
            with self.assertRaises(RiskError):c.dispatch(trade,e,ex,bot,now,limit='10',strategy_inputs=si,reduction_inputs=ri)
        self.assertEqual(len(ex._api.requests),1)

    def test_rd1_pair_breach_uses_partial_native_exit_and_claims_parent_action(self):
        c,wallet,p,guard,trade,bot,e,now,ri,si=self.position(name='F1Momentum168Native',price='30')
        ex,raw=self.exchange(c,trade,now,'1',price='30')
        with wallet.clock.at(now):
            targets,_,_=build_position_targets(p,e,bot.strategy,ri,now,{})
            target=D(decimal_text(ex.amount_to_contract_precision(trade.pair,float(targets[trade.pair]))))
            quantity=D(5)-target;raw['origQty']=str(quantity)
            aid=c.dispatch(trade,e,ex,bot,now,limit='30',strategy_inputs=si,reduction_inputs=ri)
        self.assertEqual(c.s['actions'][aid]['action']['rd1_parent_action']['kind'],'REDUCE_EXPOSURE')
        self.assertEqual(trade.exit_reason,'rd1_reduction')
        values=(c,wallet,p,guard,trade,bot,e,now,ri,si,ex,raw,aid,target,quantity)
        checked,_,result,_,_=self.fill_reduction(values)
        self.assertFalse(result['requires_further_exit'])
        self.assertFalse(c.risk.exposure(checked.snapshot,c.risk.equity(checked.snapshot))['breaches'])

    def test_component_projection_respects_cluster_count_and_frozen_order(self):
        _,wallet,p,_,trade,bot,e,now,ri,_=self.position(name='F1Momentum168Native')
        pairs=p.risk.plan['markets']['spot']['pairs'][:7]
        positions=tuple(Position('component-'+pair,pair,D(20),D(10),D(0)) for pair in pairs)
        snap=replace(e.snapshot,balance=D(0),positions=positions,quotes=tuple(Quote(pair,D(10),now,'spot_bid') for pair in pairs))
        e=replace(e,snapshot=snap,position_bindings={pos.position_id:i+1 for i,pos in enumerate(positions)})
        with wallet.clock.at(now):targets,_,proof=build_position_targets(p,e,bot.strategy,ri,now,{})
        equity=p.risk.equity(snap)
        self.assertEqual(targets[pairs[-1]],0)
        self.assertLessEqual(sum(targets.values())*10,equity*D('.3')+D('1e-20'))
        self.assertTrue(all(q*10<=equity*D('.1') for q in targets.values()))
        self.assertEqual(proof['correlation_complete'],False)

    def test_component_warning_target_limits_gross_even_with_independent_clusters(self):
        _,wallet,p,_,trade,bot,e,now,ri,_=self.position(name='F1Momentum168Native')
        pairs=p.risk.plan['markets']['spot']['pairs'][:6]
        positions=tuple(Position('component-'+pair,pair,D(20),D(10),D(0)) for pair in pairs)
        correlations=Correlations(now,720,tuple((a,b,D(0)) for a,b in itertools.combinations(sorted(p.risk.universe),2)))
        snap=replace(e.snapshot,balance=D(0),positions=positions,quotes=tuple(Quote(pair,D(10),now,'spot_bid') for pair in pairs),correlations=correlations)
        e=replace(e,snapshot=snap,position_bindings={pos.position_id:i+1 for i,pos in enumerate(positions)})
        p.risk.latch('hwm_warning',now)  # explicit component fixture; no claimed account drawdown history
        with wallet.clock.at(now):targets,_,proof=build_position_targets(p,e,bot.strategy,ri,now,{})
        self.assertTrue(proof['correlation_complete'])
        self.assertEqual(D(proof['gross_fraction']),D('.3'))
        self.assertLessEqual(sum(targets.values())*10,p.risk.equity(snap)*D('.3')+D('1e-20'))

    def test_different_account_and_risk_cost_cases_cannot_define_factor_budget(self):
        _,wallet,p,_,trade,bot,e,now,ri,_=self.position()
        p.exit_rate=D('.0036')
        with wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError,'cost scenarios differ'):
                build_position_targets(p,e,bot.strategy,ri,now,{})

    def test_daily_target_commit_ack_loss_recovers_before_first_request_without_rescaling(self):
        c,wallet,p,_,trade,bot,e,now,ri,si=self.position()
        ex,raw=self.exchange(c,trade,now,'1')
        with wallet.clock.at(now):
            targets,_,_=build_position_targets(p,e,bot.strategy,ri,now,{})
            target=D(decimal_text(ex.amount_to_contract_precision(trade.pair,float(targets[trade.pair]))))
            raw['origQty']=str(D(5)-target)
            save=c.store.save
            def fail_after_daily(state):
                save(state)
                if state.get('daily_position_targets') and not state['actions']:
                    raise OSError('synthetic daily commit acknowledgement lost')
            with patch.object(c.store,'save',side_effect=fail_after_daily):
                with self.assertRaisesRegex(StateError,'not acknowledged'):
                    c.dispatch(trade,e,ex,bot,now,limit='10',strategy_inputs=si,reduction_inputs=ri)
        self.assertEqual(ex._api.requests,[])
        c.close();c=NativeRiskExit(self.root/'position-exits',self.gate,wallet);self.opened.append(c);ex.controller=c
        retained=copy.deepcopy(c.s['daily_position_targets'])
        with wallet.clock.at(now):
            aid=c.dispatch(trade,e,ex,bot,now,limit='10',strategy_inputs=si,reduction_inputs=ri)
        self.assertEqual(c.s['daily_position_targets'],retained)
        self.assertEqual(D(c.s['actions'][aid]['target_remaining_quantity']),target)
        self.assertEqual(len(ex._api.requests),1)

    def test_rd1_reduction_does_not_depend_on_f2_factor_data_or_signal_evaluation(self):
        c,wallet,p,_,trade,bot,e,now,ri,_=self.position(price='30')
        ri=replace(ri,hourly={trade.pair:object()})
        ex,raw=self.exchange(c,trade,now,'1',price='30')
        with wallet.clock.at(now):
            targets,daily,_=build_position_targets(p,e,bot.strategy,ri,now,{},apply_factor=False)
            target=D(decimal_text(ex.amount_to_contract_precision(trade.pair,float(targets[trade.pair]))))
            raw['origQty']=str(D(5)-target)
            aid=c.dispatch(trade,e,ex,bot,now,limit='30',reduction_inputs=ri)
        self.assertEqual(daily,{})
        self.assertFalse(c.s['actions'][aid]['reduction_evaluation']['factor_policy_enabled'])
        self.assertEqual(c.s['actions'][aid]['action']['rd1_parent_action']['kind'],'REDUCE_EXPOSURE')


if __name__=='__main__':unittest.main()
