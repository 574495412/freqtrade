"""Independent receipt/execution clocks with actual native wallets and orders.

All account, quote and transport inputs are explicitly synthetic. No network.
"""
import copy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE/'sources'))
import test_native_wallets as wf
import test_native_delayed_decisions as df
from native_wallets import NativeWalletBridge
from native_entries import NativeEntryGate
from freqtrade.persistence import Trade
from freqtrade.wallets import Wallet
from rd1 import RiskError, StateError, digest, stamp


class ReceiptRefreshTests(unittest.TestCase):
    for _name in ('setUp','tearDown','provider','checkpoint','receipts','event','account',
                  'execute','receipt','harness','boot','fill_cycle'):
        locals()[_name] = getattr(wf.NativeWalletTests,_name)
    del _name

    def ready(self, market='spot'):
        c = self.harness(market)
        with c[-1].clock.at(wf.MIDNIGHT):
            self.boot(c[6],c[-1],c[7])
        return c

    def test_spot_reparse_preserves_source_checkpoint_and_account_values(self):
        c = self.ready(); provider,wallet = c[6],c[-1]
        source = copy.deepcopy(wallet.s['frames']['boot'])
        checkpoint = copy.deepcopy(provider.s)
        now = wf.MIDNIGHT+timedelta(seconds=15)
        with wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError,'explicit refresh'):
                wallet.require_entry_clear(now)
            record = wallet.refresh_verified(now)
            wallet.require_entry_clear(now)
            self.assertIsNone(wallet.refresh_verified(now))
        self.assertEqual(record['state'],'COMPLETE')
        self.assertEqual(record['source_received_at'],stamp(wf.MIDNIGHT))
        self.assertEqual(wallet.wallets._last_wallet_refresh,now)
        self.assertEqual(wallet.wallets.get_total('USDT'),1000)
        self.assertEqual(provider.s,checkpoint)
        source['native_refresh_at'] = stamp(now)
        self.assertEqual(wallet.s['frames']['boot'],source)
        self.assertEqual(len(wallet.s['refreshes']),1)

    def test_futures_refresh_preserves_real_filled_inventory_cash_and_ledger(self):
        wallet,provider,_,trade,_,_,_,_,at = self.fill_cycle('futures')
        checkpoint = copy.deepcopy(provider.s)
        positions = copy.deepcopy(wallet.wallets.get_all_positions())
        now = at+timedelta(seconds=3)
        with wallet.clock.at(now):
            wallet.refresh_verified(now); wallet.require_entry_clear(now)
        self.assertEqual(provider.s,checkpoint)
        self.assertEqual(provider.ledger.last_sequence,1)
        self.assertEqual(wallet.wallets.get_all_positions(),positions)
        self.assertEqual(wallet.wallets.get_total('USDT'),999.95)
        self.assertEqual(wallet.wallets.get_owned(trade.pair,'BTC'),5)

    def test_expired_source_and_backward_execution_time_cannot_refresh(self):
        c = self.ready(); wallet = c[-1]
        now = wf.MIDNIGHT+timedelta(seconds=4)
        with wallet.clock.at(now): wallet.refresh_verified(now)
        with wallet.clock.at(now-timedelta(seconds=1)):
            with self.assertRaisesRegex(RiskError,'regressed'):wallet.refresh_verified(now-timedelta(seconds=1))
        expired = wf.MIDNIGHT+timedelta(seconds=16)
        with wallet.clock.at(expired):
            with self.assertRaisesRegex(RiskError,'stale'):wallet.refresh_verified(expired)
        self.assertEqual(wallet.wallets._last_wallet_refresh,now)
        self.assertEqual(len(wallet.s['refreshes']),1)

    def test_young_receipt_does_not_rejuvenate_older_balance_or_quote(self):
        c = self.ready(); provider,wallet,e = c[6],c[-1],c[7]
        at = wf.MIDNIGHT+timedelta(seconds=10)
        e = replace(e,snapshot=replace(e.snapshot,at=at,orders_at=at,cursor=1))
        # Balance and quotes remain at midnight, ten seconds before receipt.
        with wallet.clock.at(at):self.boot(provider,wallet,e,'lagged-source')
        now = wf.MIDNIGHT+timedelta(seconds=16)
        with wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError,'stale_data'):wallet.refresh_verified(now)
        self.assertEqual(wallet.wallets._last_wallet_refresh,at)

    def test_mutated_native_wallet_source_and_checkpoint_are_not_silently_repaired(self):
        c = self.ready(); provider,wallet = c[6],c[-1]
        now = wf.MIDNIGHT+timedelta(seconds=1)
        original = wallet.wallets._wallets['USDT']
        with wallet.clock.at(now), patch.object(wallet.wallets,'update') as update:
            wallet.wallets._wallets['USDT'] = Wallet('USDT',1200,0,1200)
            with self.assertRaisesRegex(RiskError,'amounts differ'):wallet.refresh_verified(now)
            wallet.wallets._wallets['USDT'] = original
            wallet.source.frame['balances']['USDT']['free'] = 1200
            with self.assertRaisesRegex(RiskError,'payload changed'):wallet.refresh_verified(now)
            wallet.source.frame['balances']['USDT']['free'] = 1000.
            wallet.s['frames']['boot']['raw']['receipt']['source_id'] = 'tampered'
            with self.assertRaisesRegex(RiskError,'source receipt changed'):wallet.refresh_verified(now)
            wallet.s['frames']['boot']['raw']['receipt']['source_id'] = 'synthetic-wallet-receipt'
            provider.s['checkpoints']['0']['audit']['event_sequence'] = 10
            with self.assertRaisesRegex(RiskError,'watermark'):wallet.refresh_verified(now)
            update.assert_not_called()

    def test_pending_whole_account_batch_blocks_refresh_before_native_update(self):
        c = self.ready(); gate,wallet = c[1],c[-1]
        gate.s['pending_account_batch'] = 'synthetic-incomplete'
        now = wf.MIDNIGHT+timedelta(seconds=1)
        with wallet.clock.at(now), patch.object(wallet.wallets,'update') as update:
            with self.assertRaisesRegex(RiskError,'whole-account'):wallet.refresh_verified(now)
            update.assert_not_called()

    def test_native_update_failure_revokes_permission_until_same_source_revalidation(self):
        c = self.ready(); provider,wallet,e = c[6],c[-1],c[7]
        before = copy.deepcopy(provider.s)
        now = wf.MIDNIGHT+timedelta(seconds=2)
        with wallet.clock.at(now):
            with patch.object(wallet.wallets,'update',side_effect=OSError('synthetic update failure')):
                with self.assertRaisesRegex(OSError,'update failure'):wallet.refresh_verified(now)
            self.assertIsNone(wallet._checked_evidence)
            self.assertEqual(wallet.s['state'],'REFRESH_PENDING')
            with self.assertRaisesRegex(RiskError,'pending'):wallet.require_entry_clear(now)
            checked,_ = provider.checkpoint(e,self.receipts(e),now,complete_sequence=0,
                complete_from_inception=True,funding_complete=True)
            wallet.stage('boot',self.receipt(e),e,now)
            wallet.confirm(checked,now); wallet.require_entry_clear(now)
        self.assertEqual(provider.s,before)
        self.assertEqual(wallet._checked_evidence.snapshot.at,wf.MIDNIGHT)
        self.assertEqual(next(iter(wallet.s['refreshes'].values()))['state'],'PREPARED')

    def test_completion_ack_loss_requires_reopen_and_explicit_original_receipt(self):
        c = self.ready(); life,gate,provider,e,guard,wallet = c[0],c[1],c[6],c[7],c[8],c[9]
        now = wf.MIDNIGHT+timedelta(seconds=2)
        before = copy.deepcopy(provider.s)
        save = wallet.store.save
        def lose_complete(state):
            save(state)
            if state['state']=='VERIFIED':raise OSError('synthetic completion acknowledgement lost')
        with wallet.clock.at(now), patch.object(wallet.store,'save',side_effect=lose_complete):
            with self.assertRaisesRegex(StateError,'not acknowledged'):wallet.refresh_verified(now)
            self.assertIsNone(wallet._checked_evidence)
            with self.assertRaisesRegex(StateError,'unavailable'):wallet.require_entry_clear(now)
        wallet.close();gate.close()
        gate = NativeEntryGate(self.root/'spot-receipts',life,after_fill_guard=guard)
        self.opened.append(gate)
        wallet = NativeWalletBridge(self.root/'wallet',provider,[wf.ef.market_fixture('spot')])
        self.opened.append(wallet);wallet.attach(gate)
        with wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError,'reconstructed'):wallet.refresh_verified(now)
            checked,_ = provider.checkpoint(e,self.receipts(e),now,complete_sequence=0,
                complete_from_inception=True,funding_complete=True)
            wallet.stage('boot',self.receipt(e),e,now);wallet.confirm(checked,now)
            wallet.require_entry_clear(now)
        self.assertEqual(provider.s,before)
        self.assertEqual(len(wallet.s['refreshes']),1)
        self.assertEqual(wallet._checked_evidence.snapshot.at,wf.MIDNIGHT)

    def test_prepared_ack_loss_never_calls_native_update(self):
        c = self.ready();wallet = c[-1]
        now = wf.MIDNIGHT+timedelta(seconds=1)
        save = wallet.store.save
        def lose_prepared(state):
            save(state);raise OSError('synthetic prepared acknowledgement lost')
        with wallet.clock.at(now), patch.object(wallet.wallets,'update') as update:
            with patch.object(wallet.store,'save',side_effect=lose_prepared):
                with self.assertRaisesRegex(StateError,'not acknowledged'):wallet.refresh_verified(now)
            update.assert_not_called()
            self.assertIsNone(wallet._checked_evidence)
            with self.assertRaisesRegex(StateError,'unavailable'):wallet.require_entry_clear(now)
        self.assertEqual(wallet.wallets._last_wallet_refresh,wf.MIDNIGHT)

    def test_unknown_initial_submission_cannot_refresh_old_account(self):
        c = self.ready();life,wallet = c[0],c[-1]
        life.s['requests']['synthetic-unknown'] = {'lifecycle':{'state':'SUBMITTED_UNKNOWN'}}
        now = wf.MIDNIGHT+timedelta(seconds=1)
        with wallet.clock.at(now), patch.object(wallet.wallets,'update') as update:
            with self.assertRaisesRegex(RiskError,'initial-order observation'):wallet.refresh_verified(now)
            update.assert_not_called()


class ReceiptDispatchTests(unittest.TestCase):
    for _name in ('setUp','tearDown','provider','checkpoint','receipts','bars','setup_runtime',
                  'account','prepare_response','reports','fill_events','fixture','make','ready',
                  'wallet_receipt','financial','dispatch','observe','advance','enter_two','exit_exchange',
                  'fill_exit','fresh_liquidity','response','first','feedback'):
        locals()[_name] = getattr(df.DelayedDecisionTests,_name)
    del _name

    def delayed_dispatch(self,name):
        c = self.first(name)
        received = c.now+timedelta(seconds=1)
        now = c.now+timedelta(seconds=3)
        self.feedback(c,received,2)
        checkpoint = copy.deepcopy(c.provider.s)
        source = copy.deepcopy(c.wallet.s['frames'][c.wallet.s['current']]['raw'])
        with c.wallet.clock.at(now):
            liquidity = self.fresh_liquidity(c,now)
            rid,_ = self.response(c,1,now,liquidity)
            result = self.dispatch(c,now,entry_exchange=c.ex,entry_liquidity=liquidity,allow_entry=True)
        self.assertEqual(result['state'],'DISPATCHED_ENTRY')
        self.assertEqual(c.provider.s,checkpoint)
        self.assertEqual(c.wallet.s['frames'][c.wallet.s['current']]['raw'],source)
        self.assertEqual(c.wallet._checked_evidence.snapshot.at,received)
        self.assertEqual(c.wallet.wallets._last_wallet_refresh,now)
        trade = Trade.session.get(Trade,c.gate.s['requests'][rid]['native_binding']['trade_id'])
        self.assertEqual(trade.open_date_utc,now)
        self.assertEqual(c.gate.s['factor_decisions'][rid]['decision_at'],stamp(c.now))
        later = now+timedelta(seconds=20)
        with c.wallet.clock.at(later):self.assertEqual(self.dispatch(c,later)['state'],'WAIT_ACCOUNT')
        self.assertEqual(len(c.ex._api.requests),2)
        self.assertEqual(len(c.wallet.s['refreshes']),1)

    def test_f1_original_decision_receipt_and_native_dispatch_have_three_distinct_times(self):
        self.delayed_dispatch('F1Momentum168Native')

    def test_f2_original_decision_receipt_and_native_dispatch_have_three_distinct_times(self):
        self.delayed_dispatch('F2Volatility24Native')

    def test_first_late_dispatch_cannot_invent_boundary_registration(self):
        c = self.ready();now = c.now+timedelta(seconds=2)
        with c.wallet.clock.at(now):
            result = self.dispatch(c,now,entry_exchange=c.ex,entry_liquidity=self.fresh_liquidity(c,now),allow_entry=True)
        self.assertEqual(result['state'],'IDLE')
        self.assertEqual(result['entry_timing'],'no_active_boundary_registration')
        self.assertFalse(c.gate.s.get('factor_schedules'))
        self.assertEqual(c.ex._api.requests,[])

    def test_registered_driver_cannot_be_bypassed_to_refresh_wallet(self):
        c = self.ready();now = c.now+timedelta(seconds=1)
        with c.wallet.clock.at(now):
            with self.assertRaisesRegex(StateError,'driver must own'):c.wallet.refresh_verified(now)
        self.assertFalse(c.wallet.s.get('refreshes'))

    def test_account_received_earlier_can_be_processed_and_dispatched_later(self):
        c = self.first();received = c.now+timedelta(seconds=1)
        now = c.now+timedelta(seconds=3)
        reports = self.reports(c,received)
        e,receipt,quotes = self.account(c,reports,received,2)
        with c.wallet.clock.at(now):
            c.driver.observe('delayed-processing',e,receipt,quotes,reports,(),c.bot,now,
                complete_sequence=0,complete_from_inception=True,funding_complete=True)
            original = copy.deepcopy(c.provider.s)
            liquidity = self.fresh_liquidity(c,now)
            self.response(c,1,now,liquidity)
            result = self.dispatch(c,now,entry_exchange=c.ex,entry_liquidity=liquidity,allow_entry=True)
        self.assertEqual(result['state'],'DISPATCHED_ENTRY')
        self.assertEqual(c.wallet._checked_evidence.snapshot.at,received)
        self.assertEqual(c.wallet.wallets._last_wallet_refresh,now)
        self.assertEqual(c.provider.s,original)
        self.assertFalse(c.wallet.s.get('refreshes'))

    def test_delayed_dispatch_rejects_expired_original_account(self):
        c = self.first();received = c.now+timedelta(seconds=1)
        self.feedback(c,received,2)
        now = received+timedelta(seconds=16)
        with c.wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError,'stale'):
                self.dispatch(c,now,entry_exchange=c.ex,entry_liquidity=self.fresh_liquidity(c,now),allow_entry=True)
        self.assertEqual(len(c.ex._api.requests),1)
        self.assertFalse(c.wallet.s.get('refreshes'))


if __name__=='__main__':unittest.main()
