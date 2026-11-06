"""Official fixed strategy exit evaluation and local native submission."""
import copy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'sources'))
from native_strategy_exits import ExitBarInputs, ExitRate, evaluate_fixed_exit, TIMING
from native_exits import NativeRiskExit, LocalExitExchange
from native_entries import LocalBinanceTape
from rd1 import RiskError, StateError, digest
from freqtrade.persistence import Trade
from freqtrade.enums import ExitType
from test_native_factors import resolved
import test_native_entries as ef
import test_native_wallets as wf


def bars(end,minutes,values):
    values=np.asarray(values,dtype=float)
    dates=pd.date_range(end=pd.Timestamp(end)-pd.Timedelta(minutes=minutes),periods=len(values),freq=f'{minutes}min')
    return pd.DataFrame({'date':dates,'open':values,'high':values*1.01,'low':values*.99,'close':values,'volume':100.})


class NativeStrategyExitTests(unittest.TestCase):
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
    fill_cycle=wf.NativeWalletTests.fill_cycle

    def harness(self,market='spot'):
        result=wf.NativeWalletTests.harness(self,market);self.gate=result[1];return result

    def position(self,*,name='F1Momentum168Native',rate='9.4',market='spot'):
        wallet,provider,guard,trade,bot,e,_,_,now=self.fill_cycle(market)
        bot.strategy=resolved(name,(trade.pair,))
        now+=timedelta(seconds=1)
        e=replace(e,snapshot=replace(e.snapshot,at=now,balance_at=now,orders_at=now,cursor=3,
            quotes=tuple(replace(q,at=now,price=D(rate)) if q.pair==trade.pair else replace(q,at=now) for q in e.snapshot.quotes)))
        with wallet.clock.at(now):
            wallet.stage('valuation',self.receipt(e,quantity='5',cash_used='50' if market=='futures' else '0',collateral='50',upnl=str(D(5)*(D(rate)-10))),e,now)
            checked,_=self.checkpoint(provider,e);wallet.confirm(checked,now)
        controller=NativeRiskExit(self.root/'strategy-exit',self.gate,wallet,create=True);self.opened.append(controller)
        return controller,wallet,provider,guard,trade,bot,checked,now

    def exchange(self,c,trade,now,rate,*,rd1=False):
        if rd1:
            action=next(a for a in c.risk.actions() if a['kind']=='EXIT_POSITION')
            aid=action['id']
        else:
            aid=digest({'account':c.risk.s['identity'],'trade_id':trade.id,'kind':'STRATEGY_EXIT'})[:24]
        client='rd1x-'+digest({'account':c.risk.s['identity'],'action':aid})[:28]
        raw={'symbol':'BTCUSDT','orderId':200,'clientOrderId':client,'time':int(now.timestamp()*1000),'transactTime':int(now.timestamp()*1000),
            'updateTime':int(now.timestamp()*1000),'price':rate,'origQty':'5','executedQty':'0','cummulativeQuoteQty':'0','cumQuote':'0','avgPrice':'0',
            'status':'NEW','timeInForce':'GTC','type':'LIMIT','side':'SELL','reduceOnly':True}
        tape=LocalBinanceTape([ef.market_fixture(c.risk.market)],[(raw,now)],allowed_methods=('POST','GET'))
        ex=LocalExitExchange(tape,c);self.opened.append(ex)
        return ex,raw

    def stop_cycle(self,market):
        c,wallet,p,guard,trade,bot,e,now=self.position(market=market)
        ex,raw=self.exchange(c,trade,now,'9.4')
        rate=ExitRate(trade.pair,'9.4',now,now,'synthetic-independent-bid','synthetic') if market=='futures' else None
        with wallet.clock.at(now):
            aid=c.dispatch(trade,e,ex,bot,now,limit='9.4',strategy_inputs=ExitBarInputs(None,{},'synthetic-missing-signal-bars',exit_rate=rate))
        self.assertEqual(trade.exit_reason,'stop_loss')
        self.assertEqual(c.s['actions'][aid]['exit_type'],'stop_loss')
        self.assertEqual(trade.orders[-1].ft_order_tag,c.s['actions'][aid]['client_order_id'])
        self.assertNotIn('daily_loss',c.risk.s['latches'])
        self.assertFalse(c.s['actions'][aid]['strategy_evaluation']['signal_ready'])
        now+=timedelta(seconds=1)
        terminal=copy.deepcopy(raw);terminal.update(status='FILLED',executedQty='5',cummulativeQuoteQty='47',cumQuote='47',avgPrice='9.4',updateTime=int(now.timestamp()*1000))
        ex._api.responses=[(terminal,now)]
        with wallet.clock.at(now):ex.query(aid,now)
        p.append(self.event(trade,2,quantity='-5',fee='.047',seconds=3,order_id='200',price='9.4'))
        e=self.account(c.envelope(aid,now),balance='996.903',cursor=4,market=market)
        with wallet.clock.at(now):
            bot.wallets=wallet.stage('stop-filled',self.receipt(e),e,now)
            c.apply_filled(aid,bot,now)
            checked,audit=self.checkpoint(p,e);wallet.confirm(checked,now)
            result=c.reconcile(aid,checked,now)
        self.assertEqual(trade.exit_reason,'stop_loss')
        self.assertFalse(trade.is_open)
        self.assertEqual(D(audit['observed_equity']),D('996.903'))
        self.assertEqual(D(audit['fees_paid']),D('.097'))
        self.assertFalse(result['requires_further_exit'])
        return c,ex

    def test_spot_native_five_percent_stop_without_signal_data_reconciles_full_account(self):
        self.stop_cycle('spot')

    def test_futures_native_stop_preserves_reason_and_reduce_only(self):
        _,ex=self.stop_cycle('futures')
        self.assertTrue(ex._api.requests[0]['params']['reduceOnly'])

    def test_official_roi_is_preserved_for_control_and_f2(self):
        c,wallet,_,_,trade,bot,e,now=self.position(name='F2Volatility24Native',rate='16')
        ex,_=self.exchange(c,trade,now,'16')
        with wallet.clock.at(now):
            roi,proof=evaluate_fixed_exit(resolved('AverageRD1ControlNative',(trade.pair,)),trade,e,ExitBarInputs(None,{},'synthetic-no-bars'),now)
            aid=c.dispatch(trade,e,ex,bot,now,limit='16',strategy_inputs=ExitBarInputs(None,{},'synthetic-no-bars'))
        self.assertEqual(roi[0].exit_type,ExitType.ROI)
        self.assertEqual(c.s['actions'][aid]['strategy_evaluation']['ordered_exits'],proof['ordered_exits'])
        self.assertEqual(trade.exit_reason,'roi')

    def test_f1_has_no_roi_and_no_exit_creates_no_order_or_attempt(self):
        c,wallet,_,_,trade,bot,e,now=self.position(rate='16')
        ex,_=self.exchange(c,trade,now,'16')
        with wallet.clock.at(now):
            self.assertIsNone(c.dispatch(trade,e,ex,bot,now,limit='16',strategy_inputs=ExitBarInputs(None,{},'synthetic-no-bars')))
        self.assertEqual(ex._api.requests,[])
        self.assertEqual(c.s['actions'],{})
        self.assertEqual(len(c.s['strategy_evaluations']),1)

    def test_closed_ema_cross_uses_official_signal_and_future_bars_do_not_change_exit(self):
        c,wallet,_,_,trade,bot,e,now=self.position(name='F2Volatility24Native',rate='10')
        # Fixed synthetic rising history followed by a sharp last closed decline.
        main=bars(ef.T,240,list(np.linspace(8,12,40))+[1.])
        future=bars(ef.T+timedelta(hours=8),240,[1000.,1.])
        inputs=ExitBarInputs(main,{},'synthetic-EMA-fixture')
        with wallet.clock.at(now):
            exits,proof=evaluate_fixed_exit(bot.strategy,trade,e,inputs,now)
            again,other=evaluate_fixed_exit(bot.strategy,trade,e,replace(inputs,main=pd.concat([main,future],ignore_index=True)),now)
        self.assertEqual(exits[0].exit_type,ExitType.EXIT_SIGNAL)
        self.assertEqual(proof,other)
        self.assertEqual([x.exit_type for x in exits],[x.exit_type for x in again])
        ex,_=self.exchange(c,trade,now,'10')
        with wallet.clock.at(now):aid=c.dispatch(trade,e,ex,bot,now,limit='10',strategy_inputs=inputs)
        self.assertEqual(c.s['actions'][aid]['exit_type'],'exit_signal')
        self.assertEqual(trade.exit_reason,'exit_signal')

    def test_daily_f1_selection_exits_only_at_next_five_minute_boundary(self):
        _,wallet,_,_,trade,bot,e,_=self.position(rate='10')
        day=pd.Timestamp(ef.T).normalize()+pd.Timedelta(days=1)
        main=bars(day+pd.Timedelta(minutes=5),5,[10.,10.])
        hours=bars(day,60,np.linspace(20,10,169))
        inputs=ExitBarInputs(main,{trade.pair:hours},'synthetic-negative-momentum')
        proofs=[]
        for now in (day.to_pydatetime(),(day+pd.Timedelta(minutes=5)).to_pydatetime()):
            ev=replace(e,snapshot=replace(e.snapshot,quotes=tuple(replace(q,at=now) for q in e.snapshot.quotes)))
            with wallet.clock.at(now):out,proof=evaluate_fixed_exit(bot.strategy,trade,ev,inputs,now)
            proofs.append((out,proof))
        self.assertEqual(proofs[0][0],[])
        self.assertEqual(proofs[1][0][0].exit_type,ExitType.EXIT_SIGNAL)
        self.assertEqual(proofs[1][1]['exit_tag'],'F1_daily_selection')

    def test_strategy_veto_retains_unknown_attempt_and_cannot_fall_through_to_second_exit(self):
        c,wallet,_,_,trade,bot,e,now=self.position()
        ex,_=self.exchange(c,trade,now,'9.4')
        with wallet.clock.at(now),patch.object(bot.strategy,'confirm_trade_exit',return_value=False) as confirm:
            with self.assertRaisesRegex(RiskError,'did not complete submission'):
                c.dispatch(trade,e,ex,bot,now,limit='9.4',strategy_inputs=ExitBarInputs(None,{},'synthetic-no-bars'))
            with self.assertRaisesRegex(StateError,'already attempted'):
                c.dispatch(trade,e,ex,bot,now,limit='9.4',strategy_inputs=ExitBarInputs(None,{},'synthetic-no-bars'))
        self.assertEqual(confirm.call_count,1)
        self.assertEqual(ex._api.requests,[])

    def test_rd1_full_exit_has_priority_without_factor_inputs(self):
        c,wallet,_,_,trade,bot,e,now=self.position(rate='5')
        with wallet.clock.at(now):c.risk.evaluate(e.snapshot,now)
        ex,_=self.exchange(c,trade,now,'5',rd1=True)
        with wallet.clock.at(now),patch.object(bot.strategy,'confirm_trade_exit',return_value=False) as veto:
            aid=c.dispatch(trade,e,ex,bot,now,limit='5',strategy_inputs=object())
        self.assertEqual(c.s['actions'][aid]['action']['kind'],'EXIT_POSITION')
        veto.assert_not_called()

    def test_changed_stop_or_roi_settings_cannot_be_used_as_fixed_strategy(self):
        c,wallet,_,_,trade,bot,e,now=self.position()
        ex,_=self.exchange(c,trade,now,'9.4')
        bot.strategy.stoploss=-.2
        with wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError,'settings differ'):
                c.dispatch(trade,e,ex,bot,now,limit='9.4',strategy_inputs=ExitBarInputs(None,{},'synthetic-no-bars'))
        self.assertEqual(ex._api.requests,[])

    def test_missing_ema_bar_suppresses_signal_without_disabling_stop(self):
        _,wallet,_,_,trade,bot,e,now=self.position(name='F2Volatility24Native')
        main=bars(ef.T,240,list(np.linspace(8,12,40))+[1.]).drop(index=20)
        with wallet.clock.at(now):
            exits,proof=evaluate_fixed_exit(bot.strategy,trade,e,ExitBarInputs(main,{},'synthetic-gapped-bars'),now)
        self.assertFalse(proof['signal_ready'])
        self.assertFalse(proof['exit'])
        self.assertEqual([x.exit_type for x in exits],[ExitType.STOP_LOSS])

    def test_missing_native_stop_floor_is_rejected_without_a_request(self):
        c,wallet,_,_,trade,bot,e,now=self.position()
        ex,_=self.exchange(c,trade,now,'9.4')
        trade.stop_loss=8.;Trade.commit()
        with wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError,'common stop floor'):
                c.dispatch(trade,e,ex,bot,now,limit='9.4',strategy_inputs=ExitBarInputs(None,{},'synthetic-no-bars'))
        self.assertEqual(ex._api.requests,[])

    def test_f1_does_not_require_irrelevant_old_primary_bars_for_daily_hourly_factor(self):
        _,wallet,_,_,trade,bot,e,_=self.position(rate='10')
        day=pd.Timestamp(ef.T).normalize()+pd.Timedelta(days=1)
        now=(day+pd.Timedelta(minutes=5)).to_pydatetime()
        main=bars(now,5,[10.,10.,10.,10.]).drop(index=1)
        inputs=ExitBarInputs(main,{trade.pair:bars(day,60,np.linspace(20,10,169))},'synthetic-F1-primary-gap')
        e=replace(e,snapshot=replace(e.snapshot,quotes=tuple(replace(q,at=now) for q in e.snapshot.quotes)))
        with wallet.clock.at(now):exits,proof=evaluate_fixed_exit(bot.strategy,trade,e,inputs,now)
        self.assertTrue(proof['signal_ready'])
        self.assertEqual(exits[0].exit_type,ExitType.EXIT_SIGNAL)

    def test_futures_mark_cannot_substitute_for_missing_exit_bid(self):
        c,wallet,_,_,trade,bot,e,now=self.position(market='futures')
        ex,_=self.exchange(c,trade,now,'9.4')
        with wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError,'mark is not an exit rate'):
                c.dispatch(trade,e,ex,bot,now,limit='9.4',strategy_inputs=ExitBarInputs(None,{},'synthetic-no-bid'))
        self.assertEqual(ex._api.requests,[])

    def test_futures_exit_uses_independent_bid_while_nav_keeps_mark(self):
        c,wallet,_,_,trade,bot,e,now=self.position(market='futures',rate='9.4')
        ex,_=self.exchange(c,trade,now,'10')
        rate=ExitRate(trade.pair,'10',now,now,'synthetic-divergent-exit-bid','synthetic')
        with wallet.clock.at(now):
            result=c.dispatch(trade,e,ex,bot,now,limit='10',strategy_inputs=ExitBarInputs(None,{},'synthetic-no-bars',exit_rate=rate))
        self.assertIsNone(result)
        proof=next(iter(c.s['strategy_evaluations'].values()))
        self.assertEqual(proof['quote_price'],'9.4')
        self.assertEqual(proof['native_exit_rate']['price'],'10')
        self.assertEqual(ex._api.requests,[])


if __name__=='__main__':unittest.main()
