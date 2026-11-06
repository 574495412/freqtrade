"""Whole-account open/empty exit receipts, source economics and interrupted recovery.

All venue events are explicit synthetic fixtures; sockets are disabled by setUp.
"""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, Mock

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'sources'))
import test_native_account_runtime as ar
from native_factor_runtime import NativeFactorRuntime
from native_entries import NativeEntryGate
from native_exits import NativeRiskExit
from freqtrade.persistence import Trade
from freqtrade.freqtradebot import FreqtradeBot
from rd1 import Funding, RiskError


class ExitObservationTests(unittest.TestCase):
    # Reuse isolated DB/source constructors without inheriting their test cases.
    for _name in ('setUp','tearDown','provider','checkpoint','receipts','event','account','execute',
                  'receipt','boot','harness','ready','wallet_receipt','evidence','financial','observe',
                  'funding_cycle','dispatch','terminal'):
        locals()[_name]=getattr(ar.AccountRuntimeTests,_name)
    del _name

    def start_exit(self,market='spot',funded=False):
        if funded:c,e,now=self.funding_cycle();funding=e.snapshot.funding;cash='997.95';cursor=5
        else:
            c=self.ready(market);now=c.now;funding=();cash='949.95' if market=='spot' else '999.95';cursor=3
        now+=timedelta(seconds=1)
        e=self.evidence(c,now,cursor,cash=cash,mark='5',funding=funding)
        with c.wallet.clock.at(now):
            checked=self.observe(c,'drawdown',e)['evidence'];aid,ex,raw=self.dispatch(c,checked,now)
            opened=self.evidence(c,now,cursor+1,cash=cash,mark='5',funding=funding,open_exit=aid)
            self.observe(c,'open',opened,actions=[aid])
        return c,aid,ex,raw,opened,now

    def partial_evidence(self,c,aid,now,cursor,**kw):
        e=self.evidence(c,now,cursor,open_exit=aid,**kw)
        return replace(e,partial_base_fees={key:'0' for key in e.order_bindings})

    def test_spot_incremental_open_partials_then_terminal_and_fresh_remaining_retry(self):
        c,aid,ex,raw,opened,now=self.start_exit();now+=timedelta(seconds=1)
        c.bot.strategy.order_filled=Mock(wraps=c.bot.strategy.order_filled)
        with c.wallet.clock.at(now):
            event=self.terminal(c,aid,ex,raw,now,'2','PARTIALLY_FILLED')
            e=self.partial_evidence(c,aid,now,5,cash='959.94',quantity='3',mark='5')
            first=self.observe(c,'partial-2',e,[event],[aid])
            self.assertEqual(c.trade.amount,5)  # Native campaign updates only at terminal.
            self.assertEqual(D(first['decision'].equity),D('974.9205'))
            self.assertEqual(c.exits.s['actions'][aid]['state'],'OBSERVED_OPEN')
            self.assertEqual(c.exits.s['actions'][aid]['open_order_remaining_quantity'],'3.0')
            with self.assertRaises(RiskError):c.guard.require_entry_clear()
            with self.assertRaises(RiskError):self.dispatch(c,first['evidence'],now,venue=201)
        now+=timedelta(seconds=1)
        with c.wallet.clock.at(now):
            event=self.terminal(c,aid,ex,raw,now,'3','PARTIALLY_FILLED')
            event.update(signed_quantity='-1',fee_quote='.005',fill_id='fill-200-second')
            e=self.partial_evidence(c,aid,now,6,cash='964.935',quantity='2',mark='5')
            second=self.observe(c,'partial-3',e,[event],[aid])
            self.assertEqual(D(second['audit']['fees_paid']),D('.065'))
            c.bot.strategy.order_filled.assert_not_called()
        now+=timedelta(seconds=1)
        with c.wallet.clock.at(now):
            self.terminal(c,aid,ex,raw,now,'3','CANCELED')  # Same cumulative fills, no new ledger event.
            e=self.evidence(c,now,7,cash='964.935',quantity='2',mark='5')
            result=self.observe(c,'terminal-3',e,actions=[aid]);c.guard.require_entry_clear()
            self.assertEqual(c.trade.amount,2);self.assertTrue(c.exits.s['actions'][aid]['requires_further_exit'])
            self.assertEqual(D(result['decision'].equity),D('974.9220'))
            self.assertEqual(D(result['audit']['realized_price_pnl']),D('-15'))
            c.bot.strategy.order_filled.assert_called_once()
            with self.assertRaises(RiskError):self.dispatch(c,result['evidence'],now,venue=201)
        now+=timedelta(seconds=1)
        with c.wallet.clock.at(now):
            e=self.evidence(c,now,8,cash='964.935',quantity='2',mark='5')
            checked=self.observe(c,'fresh-retry',e)['evidence']
            self.dispatch(c,checked,now,venue=201)
            self.assertEqual(c.trade.orders[-1].amount,2)
            self.assertEqual(c.exits.s['actions'][aid]['attempt'],2)

    def test_funding_credit_during_open_partial_is_allocated_once_on_terminal(self):
        c,aid,ex,raw,opened,now=self.start_exit('futures',funded=True)
        funding=opened.snapshot.funding;now+=timedelta(seconds=1)
        with c.wallet.clock.at(now):
            event=self.terminal(c,aid,ex,raw,now,'2','PARTIALLY_FILLED')
            e=self.partial_evidence(c,aid,now,7,cash='987.94',quantity='3',mark='5',funding=funding)
            self.observe(c,'fund-partial',e,[event],[aid])
            self.assertEqual(c.trade.funding_fee_running,-2)
            self.assertIsNone(c.trade.orders[-1].funding_fee)
        now+=timedelta(seconds=1);funding=(*funding,Funding('credit-open',D('-.5'),True))
        with c.wallet.clock.at(now):
            self.terminal(c,aid,ex,raw,now,'2','PARTIALLY_FILLED')
            event=self.financial(c,5,now,'funding_settlement',funding_id='credit-open',cost_quote='-.5')
            e=self.partial_evidence(c,aid,now,8,cash='988.44',quantity='3',mark='5',funding=funding)
            self.observe(c,'fund-credit-open',e,[event],[aid])
            self.assertEqual(c.trade.funding_fees,-1.5);self.assertEqual(c.trade.funding_fee_running,-1.5)
        now+=timedelta(seconds=1)
        with c.wallet.clock.at(now):
            self.terminal(c,aid,ex,raw,now,'2','CANCELED')
            e=self.evidence(c,now,9,cash='988.44',quantity='3',mark='5',funding=funding)
            result=self.observe(c,'fund-terminal',e,actions=[aid]);c.guard.require_entry_clear()
            self.assertEqual(c.trade.amount,3);self.assertEqual(c.trade.orders[-1].funding_fee,-1.5)
            self.assertEqual(c.trade.funding_fee_running,0)
            self.assertEqual(D(result['audit']['fees_paid']),D('.06'))
            self.assertEqual(D(result['audit']['settled_funding_cash']),D('-1.5'))
            self.assertEqual(D(result['decision'].equity),D('973.4280'))

    def zero_cycle(self,market,status):
        c,aid,ex,raw,opened,now=self.start_exit(market);now+=timedelta(seconds=1)
        c.bot.strategy.order_filled=Mock(wraps=c.bot.strategy.order_filled)
        cash='949.95' if market=='spot' else '999.95'
        with c.wallet.clock.at(now):
            self.terminal(c,aid,ex,raw,now,'0',status)
            e=self.evidence(c,now,5,cash=cash,mark='5')
            with patch.object(FreqtradeBot,'handle_cancel_exit',autospec=True,side_effect=FreqtradeBot.handle_cancel_exit) as native:
                result=self.observe(c,'zero',e,actions=[aid]);native.assert_called_once()
            self.assertEqual(c.trade.amount,5);self.assertTrue(c.trade.is_open)
            self.assertIsNone(c.trade.exit_reason);self.assertIsNone(c.trade.close_rate_requested)
            self.assertFalse(c.trade.orders[-1].ft_is_open)
            self.assertTrue(c.trade.orders[-1].ft_cancel_reason)
            self.assertEqual(c.exits.s['actions'][aid]['state'],'TERMINAL_RECONCILED')
            self.assertEqual(D(result['audit']['fees_paid']),D('.05'))
            c.bot.strategy.order_filled.assert_not_called()
            with self.assertRaises(RiskError):self.dispatch(c,result['evidence'],now,venue=201)
        now+=timedelta(seconds=1)
        with c.wallet.clock.at(now):
            e=self.evidence(c,now,6,cash=cash,mark='5')
            checked=self.observe(c,'fresh-after-zero',e)['evidence'];self.dispatch(c,checked,now,venue=201)
            self.assertEqual(c.trade.orders[-1].amount,5)
        return c

    def test_spot_zero_canceled_preserves_position_and_retries_fresh(self):self.zero_cycle('spot','CANCELED')
    def test_futures_zero_expired_preserves_position_and_retries_fresh(self):self.zero_cycle('futures','EXPIRED')
    def test_futures_zero_rejected_preserves_position_and_retries_fresh(self):self.zero_cycle('futures','REJECTED')

    def zero_interrupted(self,commit_number=1,committed=True,tamper=False):
        c,aid,ex,raw,opened,now=self.start_exit();now+=timedelta(seconds=1)
        with c.wallet.clock.at(now):
            self.terminal(c,aid,ex,raw,now,'0','CANCELED')
            e=self.evidence(c,now,5,cash='949.95',mark='5');commit=Trade.commit
            calls=0
            def ack_lost():
                nonlocal calls
                calls+=1
                if calls!=commit_number:return commit()
                if committed:commit()
                raise OSError('synthetic empty-exit native commit interruption')
            with patch.object(Trade,'commit',side_effect=ack_lost):
                with self.assertRaises(OSError):self.observe(c,'recover-zero',e,actions=[aid])
            with self.assertRaises(RiskError):c.gate.require_account_batch_clear()
            c.exits.close();c.gate.close()
            gate=NativeEntryGate(self.root/'spot-receipts',c.gate.lifecycle,after_fill_guard=c.guard);self.opened.append(gate)
            c.wallet.attach(gate);c.gate=gate;c.runtime=NativeFactorRuntime(gate,c.wallet)
            c.exits=NativeRiskExit(self.root/'account-runtime-exits',gate,c.wallet);self.opened.append(c.exits)
            if tamper:
                c.trade.close_rate_requested=999;Trade.commit()
                with self.assertRaisesRegex(RiskError,'Uncertain zero-exit'):
                    self.observe(c,'recover-zero',e,actions=[aid])
                with self.assertRaises(RiskError):c.gate.require_account_batch_clear()
                return
            if committed or commit_number==2:
                with patch.object(FreqtradeBot,'update_trade_state',side_effect=AssertionError('committed zero order update repeated')):
                    result=self.observe(c,'recover-zero',e,actions=[aid])
            else:result=self.observe(c,'recover-zero',e,actions=[aid])
            self.assertEqual(c.trade.amount,5);self.assertEqual(D(result['audit']['fees_paid']),D('.05'))
            self.assertEqual(c.provider.ledger.last_sequence,1)

    def test_zero_first_commit_ack_loss_resumes_native_cleanup(self):self.zero_interrupted()
    def test_zero_final_commit_ack_loss_does_not_repeat_order_update(self):self.zero_interrupted(commit_number=2)
    def test_zero_failure_before_commit_rolls_back_and_resumes(self):self.zero_interrupted(committed=False)
    def test_zero_unexpected_cleanup_mutation_fails_closed(self):self.zero_interrupted(tamper=True)

    def test_zero_futures_terminal_preserves_unallocated_funding(self):
        c,aid,ex,raw,opened,now=self.start_exit('futures',funded=True);now+=timedelta(seconds=1)
        with c.wallet.clock.at(now):
            self.terminal(c,aid,ex,raw,now,'0','EXPIRED')
            e=self.evidence(c,now,7,cash='997.95',mark='5',funding=opened.snapshot.funding)
            result=self.observe(c,'fund-zero',e,actions=[aid])
            self.assertEqual(c.trade.funding_fees,-2);self.assertEqual(c.trade.funding_fee_running,-2)
            self.assertIsNone(c.trade.orders[-1].funding_fee)
            self.assertEqual(D(result['audit']['fees_paid']),D('.05'))
            self.assertEqual(D(result['decision'].equity),D('972.9300'))

    def test_partial_account_failure_resumes_without_reapplying_native_or_fees(self):
        c,aid,ex,raw,opened,now=self.start_exit();now+=timedelta(seconds=1)
        with c.wallet.clock.at(now):
            event=self.terminal(c,aid,ex,raw,now,'2','PARTIALLY_FILLED')
            e=self.partial_evidence(c,aid,now,5,cash='959.94',quantity='3',mark='5')
            with patch.object(c.provider,'checkpoint',side_effect=OSError('synthetic account interruption')):
                with self.assertRaises(OSError):self.observe(c,'resume-partial',e,[event],[aid])
            with self.assertRaises(RiskError):c.gate.require_account_batch_clear()
            with self.assertRaises(RiskError):ex.query(aid,now)
            c.exits.close();c.gate.close()
            gate=NativeEntryGate(self.root/'spot-receipts',c.gate.lifecycle,after_fill_guard=c.guard);self.opened.append(gate)
            c.wallet.attach(gate);c.gate=gate;c.runtime=NativeFactorRuntime(gate,c.wallet)
            c.exits=NativeRiskExit(self.root/'account-runtime-exits',gate,c.wallet);self.opened.append(c.exits)
            with patch.object(FreqtradeBot,'update_trade_state',side_effect=AssertionError('native partial repeated')):
                result=self.observe(c,'resume-partial',e,[event],[aid])
            self.assertEqual(D(result['audit']['fees_paid']),D('.06'))
            self.assertEqual(c.provider.ledger.last_sequence,2)
            with self.assertRaises(RiskError):c.guard.require_entry_clear()

    def test_unproven_partial_fill_blocks_account_and_does_not_change_native_amount(self):
        c,aid,ex,raw,opened,now=self.start_exit();now+=timedelta(seconds=1)
        with c.wallet.clock.at(now):
            self.terminal(c,aid,ex,raw,now,'2','PARTIALLY_FILLED')
            e=self.partial_evidence(c,aid,now,5,cash='959.94',quantity='3',mark='5')
            with self.assertRaisesRegex(RiskError,'fill'):
                self.observe(c,'missing-fill',e,actions=[aid])
            with self.assertRaises(RiskError):c.gate.require_account_batch_clear()
            self.assertEqual(c.trade.orders[-1].filled,0);self.assertEqual(c.provider.ledger.last_sequence,1)


if __name__=='__main__':unittest.main(verbosity=2)
