"""Synthetic fault replay through native SQLite, Wallets, bot and CCXT builders.

Network is disabled by the inherited fixture. No economic backtest is run.
"""
import copy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'native_factors_20261106'))
import test_native_exits as fixtures
from quantity_exit import dispatch_quantity_exit, InventoryBalances
from native_exits import NativeRiskExit, LocalExitExchange
from native_entries import LocalBinanceTape
from native_strategy_exits import ExitRate
from rd1 import RiskError, StateError
from freqtrade.persistence import Trade
from freqtrade.exceptions import TemporaryError


class QuantityExitTests(unittest.TestCase):
    # Reuse fixture helpers, without inheriting/rerunning unrelated old tests.
    setUp = fixtures.NativeExitTests.setUp
    tearDown = fixtures.NativeExitTests.tearDown
    provider = fixtures.NativeExitTests.provider
    checkpoint = fixtures.NativeExitTests.checkpoint
    receipts = fixtures.NativeExitTests.receipts
    event = fixtures.NativeExitTests.event
    account = fixtures.NativeExitTests.account
    execute = fixtures.NativeExitTests.execute
    receipt = fixtures.NativeExitTests.receipt
    boot = fixtures.NativeExitTests.boot
    fill_cycle = fixtures.NativeExitTests.fill_cycle
    harness = fixtures.NativeExitTests.harness
    exit_harness = fixtures.NativeExitTests.exit_harness

    def setup_exit(self, market='spot', **kw):
        self.parts = self.exit_harness(market, **kw)
        c, ex, wallet, p, guard, trade, bot, e, now, raw, action = self.parts
        self.inventory = replace(e, balance_complete=False,
            snapshot=replace(e.snapshot, cursor=e.snapshot.cursor + 1, balance=None, quotes=(), funding_complete=False))
        self.bid = ExitRate(trade.pair, '5', now, now, 'synthetic-independent-bid', 'synthetic')
        self.balances = InventoryBalances(e.account_id, 'synthetic-base-inventory', 'synthetic', now, now,
            {'BTC': {'free': '5', 'used': '0', 'total': '5'}}) if market == 'spot' else None
        return self.parts

    def submit(self):
        c, ex, wallet, p, _, trade, bot, _, now, _, _ = self.parts
        return dispatch_quantity_exit(c, trade, self.inventory, ex, bot, now,
            exit_rate=self.bid, complete_sequence=p.ledger.last_sequence, inventory_balances=self.balances)

    def assert_dispatch(self, market):
        c, ex, wallet, p, guard, trade, bot, e, now, raw, action = self.setup_exit(market)
        before = copy.deepcopy(p.s)
        with wallet.clock.at(now):
            with self.assertRaises(RiskError): c.dispatch(trade, self.inventory, ex, bot, now, limit='5')
            aid = self.submit()
            with self.assertRaises(RiskError): wallet.require_entry_clear(now)
        self.assertEqual(aid, action['id'])
        self.assertEqual(p.s, before)  # No invented cash, mark, funding or account checkpoint.
        self.assertEqual(len(ex._api.requests), 1)
        request = ex._api.requests[0]['params']
        self.assertEqual(request['quantity'], '5')
        self.assertEqual(request['side'], 'SELL')
        if market == 'futures': self.assertTrue(request['reduceOnly'])
        self.assertEqual(trade.orders[-1].order_id, '200')
        self.assertEqual(c.s['actions'][aid]['state'], 'NATIVE_BOUND_ACCOUNT_PENDING')
        self.assertFalse(c.s['actions'][aid]['account_valuation_verified'])
        self.assertIsNone(wallet._checked_evidence)

    def test_spot_native_exit_without_cash_or_marks(self): self.assert_dispatch('spot')
    def test_futures_native_reduce_only_without_cash_or_marks(self): self.assert_dispatch('futures')

    def test_missing_execution_bid_rejects_even_with_latched_risk(self):
        c, ex, wallet, _, _, _, _, _, now, _, _ = self.setup_exit()
        self.bid = None
        with wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError, 'execution bid'): self.submit()
            with self.assertRaises(RiskError): wallet.require_entry_clear(now)
        self.assertEqual(ex._api.requests, [])

    def test_reserved_base_inventory_never_reaches_native_post(self):
        c, ex, wallet, _, _, _, _, _, now, _, _ = self.setup_exit()
        self.balances = replace(self.balances, balances={'BTC': {'free':'4','used':'1','total':'5'}})
        with wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError, 'reserved'): self.submit()
        self.assertEqual(ex._api.requests, [])

    def test_stale_order_inventory_rejects(self):
        c, ex, wallet, _, _, _, _, _, now, _, _ = self.setup_exit()
        self.inventory = replace(self.inventory, snapshot=replace(self.inventory.snapshot, orders_at=now-timedelta(seconds=16)))
        with wallet.clock.at(now):
            with self.assertRaises(RiskError): self.submit()
        self.assertEqual(ex._api.requests, [])

    def test_stale_execution_bid_rejects(self):
        c, ex, wallet, _, _, _, _, _, now, _, _ = self.setup_exit()
        self.bid = replace(self.bid, at=now-timedelta(seconds=16))
        with wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError, 'execution bid'): self.submit()
        self.assertEqual(ex._api.requests, [])

    def test_incomplete_order_inventory_rejects(self):
        c, ex, wallet, _, _, _, _, _, now, _, _ = self.setup_exit()
        self.inventory = replace(self.inventory, orders_complete=False)
        with wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError, 'Incomplete source'): self.submit()
        self.assertEqual(ex._api.requests, [])

    def test_unmatched_position_quantity_rejects(self):
        c, ex, wallet, _, _, _, _, _, now, _, _ = self.setup_exit()
        self.inventory = replace(self.inventory, snapshot=replace(self.inventory.snapshot,
            positions=tuple(replace(p, quantity=D('4')) for p in self.inventory.snapshot.positions)))
        with wallet.clock.at(now):
            with self.assertRaises(RiskError): self.submit()
        self.assertEqual(ex._api.requests, [])

    def test_missing_valuation_does_not_create_a_new_full_exit(self):
        wallet, p, guard, trade, bot, e, _, _, now = self.fill_cycle('spot')
        c = NativeRiskExit(self.root/'exits', self.entry_gate, wallet, create=True)
        self.opened.append(c)
        tape = LocalBinanceTape([fixtures.ef.market_fixture('spot')], [], allowed_methods=('POST','GET'))
        ex = LocalExitExchange(tape, c)
        self.opened.append(ex)
        self.parts = (c, ex, wallet, p, guard, trade, bot, e, now, {}, None)
        self.inventory = replace(e, balance_complete=False,
            snapshot=replace(e.snapshot, cursor=e.snapshot.cursor+1, balance=None, quotes=()))
        self.bid = ExitRate(trade.pair, '5', now, now, 'synthetic-bid', 'synthetic')
        self.balances = InventoryBalances(e.account_id, 'synthetic-inventory', 'synthetic', now, now,
            {'BTC': {'free':'5','used':'0','total':'5'}})
        self.assertNotIn('daily_loss', p.risk.s['latches'])
        with wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError, 'No previously latched'): self.submit()
        self.assertEqual(ex._api.requests, [])
        self.assertEqual(c.s['actions'], {})

    def test_lost_reply_and_restart_cannot_repeat_post(self):
        c, ex, wallet, p, guard, trade, bot, e, now, raw, action = self.setup_exit(lose_reply=True)
        with wallet.clock.at(now):
            with self.assertRaises(TemporaryError): self.submit()
        Trade.session.rollback()
        c.close()
        reopened = NativeRiskExit(self.root/'exits', self.entry_gate, wallet)
        self.opened.append(reopened)
        ex.controller = reopened
        self.parts = (reopened,) + self.parts[1:]
        with wallet.clock.at(now):
            with self.assertRaisesRegex(StateError, 'earlier exit'): self.submit()
        self.assertEqual(len(ex._api.requests), 1)
        self.assertEqual(reopened.s['actions'][action['id']]['state'], 'SUBMISSION_UNKNOWN')
        self.assertEqual(len(trade.orders), 1)

    def test_intent_persist_failure_prevents_post(self):
        c, ex, wallet, _, _, _, _, _, now, _, _ = self.setup_exit()
        with wallet.clock.at(now), patch.object(c.store, 'save', side_effect=OSError('synthetic disk failure')):
            with self.assertRaisesRegex(StateError, 'not acknowledged'): self.submit()
        self.assertEqual(ex._api.requests, [])

    def test_wallet_barrier_persist_failure_prevents_post(self):
        c, ex, wallet, _, _, _, _, _, now, _, _ = self.setup_exit()
        with wallet.clock.at(now), patch.object(wallet.store, 'save', side_effect=OSError('synthetic disk failure')):
            with self.assertRaisesRegex(StateError, 'not acknowledged'): self.submit()
        self.assertEqual(ex._api.requests, [])

    def test_known_funding_preserved_without_claiming_new_coverage(self):
        c, ex, wallet, p, guard, trade, bot, e, now, raw, action = self.setup_exit('futures')
        now += timedelta(seconds=1)
        p.append(self.event(trade, 2, seconds=3, kind='funding_settlement', funding_id='known-debit', cost_quote='2'))
        trade.set_funding_fees(-2)
        Trade.commit()
        self.inventory = replace(self.inventory, snapshot=replace(self.inventory.snapshot, at=now, orders_at=now))
        self.bid = replace(self.bid, at=now, received_at=now)
        raw.update(time=int(now.timestamp()*1000), transactTime=int(now.timestamp()*1000), updateTime=int(now.timestamp()*1000))
        ex._api.responses = [(raw,now)]
        self.parts = (c, ex, wallet, p, guard, trade, bot, e, now, raw, action)
        with wallet.clock.at(now): aid = self.submit()
        self.assertEqual(trade.funding_fees, -2)
        self.assertEqual(D(c.s['actions'][aid]['running_funding_credit']), D('-2'))
        self.assertFalse(c.s['actions'][aid]['inventory_receipt']['snapshot']['funding_complete'])

    def test_native_commit_ack_loss_binds_without_second_post(self):
        c, ex, wallet, _, _, _, _, _, now, _, action = self.setup_exit()
        commit = Trade.commit
        def fail_after_commit():
            commit()
            raise RuntimeError('synthetic native acknowledgement lost')
        with wallet.clock.at(now), patch.object(Trade, 'commit', side_effect=fail_after_commit):
            with self.assertRaisesRegex(RuntimeError, 'acknowledgement lost'): self.submit()
        Trade.session.remove()
        with wallet.clock.at(now): c.bind(action['id'], now)
        self.assertEqual(len(ex._api.requests), 1)
        self.assertEqual(c.s['actions'][action['id']]['state'], 'NATIVE_BOUND_ACCOUNT_PENDING')

    def reconcile_full(self, market):
        c, ex, wallet, p, guard, trade, bot, e, now, raw, action = self.setup_exit(market)
        with wallet.clock.at(now): aid = self.submit()
        now += timedelta(seconds=1)
        terminal = copy.deepcopy(raw)
        terminal.update(status='FILLED', executedQty='5', cummulativeQuoteQty='25', cumQuote='25', avgPrice='5', updateTime=int(now.timestamp()*1000))
        ex._api.responses = [(terminal, now)]
        with wallet.clock.at(now): ex.query(aid, now)
        p.append(self.event(trade, p.ledger.last_sequence+1, quantity='-5', fee='.025', seconds=3, order_id='200', price='5'))
        account = self.account(c.envelope(aid, now), balance='974.925', cursor=self.inventory.snapshot.cursor+1, market=market)
        with wallet.clock.at(now):
            bot.wallets = wallet.stage('quantity-exit-filled', self.receipt(account), account, now)
            c.apply_filled(aid, bot, now)
            checked, audit = self.checkpoint(p, account)
            wallet.confirm(checked, now)
            result = c.reconcile(aid, checked, now)
        self.assertFalse(trade.is_open)
        self.assertEqual(result['state'], 'TERMINAL_RECONCILED')
        self.assertEqual(D(audit['observed_equity']), D('974.925'))
        self.assertEqual(D(audit['fees_paid']), D('.075'))
        self.assertEqual(len(ex._api.requests), 2)

    def test_spot_full_account_return_reconciles_actual_fill(self): self.reconcile_full('spot')
    def test_futures_full_account_return_reconciles_actual_fill(self): self.reconcile_full('futures')


if __name__ == '__main__': unittest.main()
