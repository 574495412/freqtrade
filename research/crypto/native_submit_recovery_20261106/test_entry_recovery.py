"""Fixed registered native entry -> unknown POST -> GET -> native account replay."""
import copy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'native_factors_20261106'))
import test_native_research_driver as df
from entry_recovery import query_initial_entry,recover_initial_entry,reconcile_zero_initial,reconcile_recovered_terminal_fill
from native_cancellations import LocalCancelExchange
from native_entries import LocalBinanceTape
from freqtrade.exceptions import TemporaryError
from freqtrade.persistence import Trade
from rd1 import RiskError
import ccxt


class EntryRecoveryTests(unittest.TestCase):
    for _name in ('setUp','tearDown','provider','checkpoint','receipts','bars','setup_runtime','account',
                  'prepare_response','reports','fill_events','make','wallet_receipt','financial','dispatch'):
        locals()[_name]=getattr(df.ResearchDriverTests,_name)
    del _name
    def fixture(self,market,name):return df.ResearchDriverTests.fixture(self,self.market,name)

    def unknown(self,market='spot',filled=False,status=None,original_commit=False):
        self.market=market
        name = 'F2Volatility24Native' if original_commit else 'F1Momentum168Native'
        c=df.ResearchDriverTests.ready(self,name)
        with c.wallet.clock.at(c.now):
            c.inputs = c.driver._frames(c.now)[0]
            rid=self.prepare_response(c,0)
            raw=copy.deepcopy(c.ex._api.responses[0][0]);raw['time']=int(c.now.timestamp()*1000)
            if original_commit:
                c.ex._api.responses = [(copy.deepcopy(raw), c.now)]
                commit = Trade.commit
                def lose_ack():
                    creates_trade = any(isinstance(row, Trade) for row in Trade.session.new)
                    commit()
                    if creates_trade:
                        raise OSError('synthetic original native commit acknowledgment lost')
                with patch.object(Trade,'commit',side_effect=lose_ack), self.assertRaises(OSError):
                    self.dispatch(c,c.now,entry_exchange=c.ex,entry_liquidity=c.liquidity,allow_entry=True)
            else:
                c.ex._api.responses=[ccxt.RequestTimeout('synthetic lost initial reply')]
                with self.assertRaises(TemporaryError):
                    self.dispatch(c,c.now,entry_exchange=c.ex,entry_liquidity=c.liquidity,allow_entry=True)
        Trade.session.rollback()
        self.assertEqual(len(Trade.get_trades_proxy()),int(original_commit))
        now=c.now+timedelta(seconds=1)
        if filled:
            raw.update(status='FILLED',executedQty=raw['origQty'],avgPrice='10',
                cummulativeQuoteQty=str(D(raw['origQty'])*10),cumQuote=str(D(raw['origQty'])*10))
        if status:raw['status']=status
        raw.update(reduceOnly=False,updateTime=int(now.timestamp()*1000))
        ex=LocalCancelExchange(LocalBinanceTape(list(c.ex.markets.values()),[(raw,now)],allowed_methods=('GET','DELETE')),market)
        self.opened.append(ex)
        with c.wallet.clock.at(now):report=query_initial_entry(c.driver,ex,rid,c.bot,now)
        return c,ex,rid,raw,report,now

    def full(self,market):
        c,ex,rid,raw,report,now=self.unknown(market,True)
        with c.wallet.clock.at(now):
            result=recover_initial_entry(c.driver,ex,rid,c.bot,now)
            self.assertEqual(result['state'],'NATIVE_BOUND_ACCOUNT_PENDING')
            with self.assertRaises(RiskError):c.wallet.require_entry_clear(now)
        self.assertEqual(c.life.s['requests'][rid]['lifecycle']['state'],'SUBMISSION_UNKNOWN')
        e,receipt,quotes=self.account(c,{rid:report},now,2)
        events=self.fill_events(c,{rid:report},now)
        with c.wallet.clock.at(now):
            result=c.driver.observe('recovered-initial',e,receipt,quotes,{rid:report},events,c.bot,now,
                complete_sequence=len(events),complete_from_inception=True,funding_complete=True)
        self.assertEqual(c.life.s['requests'][rid]['lifecycle']['state'],'TERMINAL_RECONCILED')
        self.assertEqual(len(Trade.get_trades_proxy()),1)
        self.assertEqual(D(result['audit']['fees_paid']),D(raw['origQty'])*D('.01'))
        self.assertEqual([r['method'] for r in c.ex._api.requests],['POST'])
        self.assertEqual([r['method'] for r in ex._api.requests],['GET'])

    def test_spot_filled_initial_recovery_reconciles_actual_fees(self):self.full('spot')
    def test_futures_filled_initial_recovery_reconciles_actual_fees(self):self.full('futures')

    def test_open_initial_recovery_is_idempotent_and_keeps_reservation(self):
        c,ex,rid,raw,report,now=self.unknown()
        with c.wallet.clock.at(now):
            first=recover_initial_entry(c.driver,ex,rid,c.bot,now)
            self.assertEqual(first,recover_initial_entry(c.driver,ex,rid,c.bot,now))
        self.assertEqual(len(Trade.get_trades_proxy()),1)
        self.assertEqual(Trade.get_trades_proxy()[0].amount,0)
        self.assertEqual(c.life.s['requests'][rid]['lifecycle']['state'],'SUBMISSION_UNKNOWN')

    def test_native_commit_ack_loss_reuses_committed_campaign(self):
        c,ex,rid,raw,report,now=self.unknown()
        commit=Trade.commit
        def lose_ack():commit();raise OSError('synthetic commit acknowledgment lost')
        with c.wallet.clock.at(now),patch.object(Trade,'commit',side_effect=lose_ack):
            with self.assertRaises(OSError):recover_initial_entry(c.driver,ex,rid,c.bot,now)
        Trade.session.remove()
        with c.wallet.clock.at(now):recover_initial_entry(c.driver,ex,rid,c.bot,now)
        self.assertEqual(len(Trade.get_trades_proxy()),1)

    def test_original_native_commit_ack_loss_recovers_existing_campaign_and_fees(self):
        c,ex,rid,raw,report,now=self.unknown('futures',filled=True,original_commit=True)
        trade_id = Trade.get_trades_proxy()[0].id
        with c.wallet.clock.at(now):
            binding = recover_initial_entry(c.driver,ex,rid,c.bot,now)['native_binding']
        self.assertEqual(binding['trade_id'],trade_id)
        e,receipt,quotes=self.account(c,{rid:report},now,2)
        events=self.fill_events(c,{rid:report},now)
        with c.wallet.clock.at(now):
            result=c.driver.observe('original-native-recovered',e,receipt,quotes,{rid:report},events,c.bot,now,
                complete_sequence=len(events),complete_from_inception=True,funding_complete=True)
        self.assertEqual(c.life.s['requests'][rid]['lifecycle']['state'],'TERMINAL_RECONCILED')
        self.assertEqual(D(result['audit']['fees_paid']),D(raw['origQty'])*D('.01'))
        self.assertEqual([r['method'] for r in c.ex._api.requests],['POST'])
        self.assertEqual([r['method'] for r in ex._api.requests],['GET'])

    def test_canceled_partial_initial_recovery_applies_fees_and_retains_position(self):
        c,ex,rid,raw,report,now=self.unknown('futures',original_commit=True)
        now+=timedelta(seconds=1)
        raw.update(status='CANCELED',executedQty='2',avgPrice='10',cummulativeQuoteQty='20',
            cumQuote='20',updateTime=int(now.timestamp()*1000))
        ex._api.responses=[(raw,now)]
        with c.wallet.clock.at(now):
            report=query_initial_entry(c.driver,ex,rid,c.bot,now)
            recover_initial_entry(c.driver,ex,rid,c.bot,now)
        e,receipt,quotes=self.account(c,{rid:report},now,2)
        events=self.fill_events(c,{rid:report},now)
        with c.wallet.clock.at(now):
            with patch.object(c.provider,'checkpoint',side_effect=OSError('synthetic checkpoint interruption')):
                with self.assertRaises(OSError):
                    reconcile_recovered_terminal_fill(c.driver,rid,'partial-canceled',e,receipt,quotes,events,c.bot,now,
                        complete_sequence=len(events),complete_from_inception=True,funding_complete=True)
            result=reconcile_recovered_terminal_fill(c.driver,rid,'partial-canceled',e,receipt,quotes,events,c.bot,now,
                complete_sequence=len(events),complete_from_inception=True,funding_complete=True)
        self.assertEqual(result['lifecycle']['state'],'TERMINAL_RECONCILED')
        self.assertEqual(D(result['audit']['fees_paid']),D('.02'))
        trade=Trade.get_trades_proxy()[0]
        self.assertEqual(trade.amount,2)
        self.assertTrue(trade.is_open)
        self.assertEqual(trade.orders[0].status,'canceled')
        self.assertEqual([r['method'] for r in c.ex._api.requests],['POST'])
        self.assertEqual([r['method'] for r in ex._api.requests],['GET','GET'])

    def test_changed_original_rules_cannot_construct_campaign(self):
        c,ex,rid,raw,report,now=self.unknown()
        ex._markets[c.universe[0]]['precision']['amount']=.1
        with c.wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError,'market rules'):recover_initial_entry(c.driver,ex,rid,c.bot,now)
        self.assertEqual(Trade.get_trades_proxy(),[])

    def test_zero_terminal_query_needs_full_account_before_release(self):
        c,ex,rid,raw,report,now=self.unknown(status='CANCELED')
        with c.wallet.clock.at(now):
            pending=recover_initial_entry(c.driver,ex,rid,c.bot,now)
        self.assertEqual(pending['state'],'ZERO_TERMINAL_ACCOUNT_PENDING')
        self.assertEqual(c.life.s['requests'][rid]['lifecycle']['state'],'SUBMISSION_UNKNOWN')
        e,receipt,quotes=self.account(c,{},now,2)
        with c.wallet.clock.at(now):
            result=reconcile_zero_initial(c.driver,rid,'zero-observed',e,receipt,quotes,c.bot,now,
                complete_sequence=0,complete_from_inception=True,funding_complete=True)
            again=reconcile_zero_initial(c.driver,rid,'zero-observed',e,receipt,quotes,c.bot,now,
                complete_sequence=0,complete_from_inception=True,funding_complete=True)
        self.assertEqual(result['lifecycle']['state'],'TERMINAL_RECONCILED')
        self.assertEqual(result,again)
        self.assertEqual(Trade.get_trades_proxy(),[])
        self.assertEqual(D(result['audit']['observed_equity']),D(1000))
        self.assertEqual(D(result['audit']['fees_paid']),0)

    def test_zero_query_with_wrong_cash_cannot_release_reservation(self):
        c,ex,rid,raw,report,now=self.unknown('futures',status='REJECTED')
        with c.wallet.clock.at(now):recover_initial_entry(c.driver,ex,rid,c.bot,now)
        e,receipt,quotes=self.account(c,{},now,2,cash='1001')
        with c.wallet.clock.at(now):
            with self.assertRaises(RiskError):
                reconcile_zero_initial(c.driver,rid,'bad-zero',e,receipt,quotes,c.bot,now,
                    complete_sequence=0,complete_from_inception=True,funding_complete=True)
        self.assertEqual(c.life.s['requests'][rid]['lifecycle']['state'],'SUBMISSION_UNKNOWN')
        self.assertEqual(Trade.get_trades_proxy(),[])
        e,receipt,quotes=self.account(c,{},now,3)
        with c.wallet.clock.at(now):
            result=reconcile_zero_initial(c.driver,rid,'corrected-zero',e,receipt,quotes,c.bot,now,
                complete_sequence=0,complete_from_inception=True,funding_complete=True)
        self.assertEqual(result['lifecycle']['state'],'TERMINAL_RECONCILED')
        self.assertIn('bad-zero',c.gate.s['requests'][rid]['zero_recovery_accounts'])

    def test_advanced_query_before_commit_failure_retries_native_update(self):
        c,ex,rid,raw,report,now=self.unknown()
        with c.wallet.clock.at(now):recover_initial_entry(c.driver,ex,rid,c.bot,now)
        now+=timedelta(seconds=1)
        raw.update(status='FILLED',executedQty=raw['origQty'],avgPrice='10',
            cummulativeQuoteQty=str(D(raw['origQty'])*10),cumQuote=str(D(raw['origQty'])*10),
            updateTime=int(now.timestamp()*1000))
        ex._api.responses=[(raw,now)]
        with c.wallet.clock.at(now):report=query_initial_entry(c.driver,ex,rid,c.bot,now)
        with c.wallet.clock.at(now),patch.object(Trade,'commit',side_effect=OSError('synthetic precommit interruption')):
            with self.assertRaises(OSError):recover_initial_entry(c.driver,ex,rid,c.bot,now)
        with c.wallet.clock.at(now):recover_initial_entry(c.driver,ex,rid,c.bot,now)
        self.assertEqual(len(Trade.get_trades_proxy()),1)
        self.assertEqual(Trade.get_trades_proxy()[0].orders[0].filled,float(raw['origQty']))
        self.assertEqual([r['method'] for r in c.ex._api.requests],['POST'])
        self.assertEqual([r['method'] for r in ex._api.requests],['GET','GET'])


if __name__=='__main__':unittest.main()
