"""Registered fixed-strategy startup, native entry/fill, quantity exit and recovery."""
import copy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent/'native_factors_20261106'))
import test_native_research_driver as driver_fixture
from quantity_driver import QuantityResearchDriver
from quantity_exit import dispatch_quantity_exit
from native_research_driver import NativeResearchDriver
from native_strategy_exits import ExitRate
from freqtrade.persistence import Trade
from rd1 import RiskError, StateError


class QuantityDriverTests(unittest.TestCase):
    for _name in ('setUp','tearDown','provider','checkpoint','receipts','bars','setup_runtime','account',
                  'prepare_response','reports','fill_events','fixture','make','wallet_receipt','financial',
                  'dispatch','observe','advance','enter_two','exit_exchange'):
        locals()[_name] = getattr(driver_fixture.ResearchDriverTests, _name)
    del _name

    def setup_exit(self):
        # Fixture construction only: register the new policy from inception.
        with patch.object(driver_fixture, 'NativeResearchDriver', QuantityResearchDriver):
            c = driver_fixture.ResearchDriverTests.ready(self)
        now = self.enter_two(c) + timedelta(seconds=1)
        first, second = Trade.get_trades_proxy()
        e = self.advance(c, now, marks={first.pair:D('5')})
        with c.wallet.clock.at(now):
            self.observe(c, 'latched-drawdown', e)
            decision = c.life.risk.evaluate(c.wallet._checked_evidence.snapshot, now)
        action = next(a for a in decision.actions if a['kind']=='EXIT_POSITION'
                      and e.position_bindings[a['target']]==first.id)
        inventory = replace(e, balance_complete=False,
            snapshot=replace(e.snapshot, cursor=e.snapshot.cursor+1, balance=None, quotes=(), funding_complete=False))
        ex, raw, aid = self.exit_exchange(c, first, now, first.amount, price='5', action_id=action['id'])
        bid = ExitRate(first.pair, '5', now, now, 'synthetic-driver-execution-bid', 'synthetic')
        return c, now, first, second, e, inventory, ex, raw, aid, bid

    def test_registered_quantity_dispatch_and_full_batch_reconciliation(self):
        c, now, first, second, e, inventory, ex, raw, aid, bid = self.setup_exit()
        with c.wallet.clock.at(now):
            with self.assertRaisesRegex(StateError, 'driver must own'):
                dispatch_quantity_exit(c.exits, first, inventory, ex, c.bot, now,
                    exit_rate=bid, complete_sequence=c.provider.ledger.last_sequence)
            actual = c.driver.quantity_exit(first, inventory, ex, c.bot, now,
                exit_rate=bid, complete_sequence=c.provider.ledger.last_sequence)
            self.assertEqual(actual, aid)
            with self.assertRaisesRegex(RiskError, 'complete account checkpoint'):
                self.dispatch(c, now, allow_entry=True)
            with self.assertRaisesRegex(RiskError, 'inventory watermark'):
                self.observe(c, 'stale-full-account', e)
        now += timedelta(seconds=1)
        terminal = copy.deepcopy(raw)
        terminal.update(status='FILLED', executedQty=raw['origQty'], cummulativeQuoteQty='48.4',
            cumQuote='48.4', avgPrice='5', updateTime=int(now.timestamp()*1000))
        ex._api.responses = [(terminal,now)]
        c.trade = first
        event = self.financial(c, 3, now, 'fill', order_id='200', fill_id='quantity-fill-200',
            signed_quantity='-9.68', price='5', fee_quote='.0484', fee_base='0')
        remaining = tuple(p for p in e.snapshot.positions if p.pair==second.pair)
        final = replace(e, snapshot=replace(e.snapshot, cursor=inventory.snapshot.cursor+1,
            at=now, balance_at=now, orders_at=now, balance=D('951.358'), positions=remaining,
            quotes=tuple(replace(q, at=now) for q in e.snapshot.quotes)),
            position_bindings={p.position_id:e.position_bindings[p.position_id] for p in remaining})
        with c.wallet.clock.at(now):
            with c.driver._scope(now, 'observe'): ex.query(aid, now)
            result = self.observe(c, 'quantity-terminal', final, [event])
        self.assertFalse(first.is_open)
        self.assertTrue(second.is_open)
        self.assertEqual(c.exits.s['actions'][aid]['state'], 'TERMINAL_RECONCILED')
        self.assertEqual(D(result['audit']['observed_wallet']), D('951.358'))
        self.assertEqual(D(result['audit']['fees_paid']), D('.242'))
        self.assertEqual(len(ex._api.requests), 2)

    def test_policy_cannot_reopen_as_old_driver(self):
        c, now, *_ = self.setup_exit()
        c.driver.close()
        with self.assertRaisesRegex(StateError, 'binding changed'): NativeResearchDriver(c.startup, c.loop)
        c.driver = QuantityResearchDriver(c.startup, c.loop)
        self.opened.append(c.driver)
        with c.wallet.clock.at(now): c.driver._attached(now, c.bot)

    def test_old_registered_driver_cannot_use_quantity_extension(self):
        c = driver_fixture.ResearchDriverTests.ready(self)
        now = self.enter_two(c)
        trade = Trade.get_trades_proxy()[0]
        with c.wallet.clock.at(now), c.driver._scope(now, 'dispatch'):
            with self.assertRaisesRegex(StateError, 'separately registered'):
                dispatch_quantity_exit(c.exits, trade, c.wallet._checked_evidence, None, c.bot, now,
                    exit_rate=None, complete_sequence=c.provider.ledger.last_sequence)
        self.assertEqual(c.exits.s['actions'], {})


if __name__=='__main__': unittest.main()
