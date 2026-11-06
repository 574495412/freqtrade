"""Registered native close and source funding corrections in temporary SQLite."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'native_quantity_exit_20261106'))
import test_quantity_driver as driver_fixture
from closed_funding import observe_closed_funding
from freqtrade.persistence import Trade
from rd1 import Funding, RiskError


class ClosedFundingTests(unittest.TestCase):
    for _name in ('setUp','tearDown','provider','checkpoint','receipts','bars','setup_runtime','account',
                  'prepare_response','reports','fill_events','fixture','make','wallet_receipt','financial',
                  'dispatch','observe','advance','enter_two','exit_exchange','setup_exit'):
        locals()[_name] = getattr(driver_fixture.QuantityDriverTests, _name)
    del _name

    def closed(self):
        c, now, trade, other, evidence, inventory, ex, raw, aid, bid = self.setup_exit()
        with c.wallet.clock.at(now):
            c.driver.quantity_exit(trade, inventory, ex, c.bot, now, exit_rate=bid,
                complete_sequence=c.provider.ledger.last_sequence)
        now += timedelta(seconds=1)
        raw.update(status='FILLED', executedQty=raw['origQty'], cummulativeQuoteQty='48.4',
            cumQuote='48.4', avgPrice='5', updateTime=int(now.timestamp()*1000))
        ex._api.responses = [(raw, now)]
        c.trade = trade
        event = self.financial(c, 3, now, 'fill', order_id='200', fill_id='funding-close-200',
            signed_quantity='-9.68', price='5', fee_quote='.0484', fee_base='0')
        positions = tuple(p for p in evidence.snapshot.positions if p.pair == other.pair)
        final = replace(evidence, snapshot=replace(evidence.snapshot, cursor=inventory.snapshot.cursor+1,
            at=now, balance_at=now, orders_at=now, balance=D('951.358'), positions=positions,
            quotes=tuple(replace(q, at=now) for q in evidence.snapshot.quotes)),
            position_bindings={p.position_id:evidence.position_bindings[p.position_id] for p in positions})
        with c.wallet.clock.at(now):
            with c.driver._scope(now, 'observe'):
                ex.query(aid, now)
            self.observe(c, 'source-close', final, [event])
        self.assertFalse(trade.is_open)
        self.assertTrue(other.is_open)
        return c, trade, other, ex, now

    def observe_funding(self, c, label, evidence, events):
        return observe_closed_funding(c.driver, label, evidence, self.wallet_receipt(evidence),
            self.receipts(evidence), {}, events, c.bot, evidence.snapshot.at,
            complete_sequence=events[-1]['sequence'], complete_from_inception=True, funding_complete=True)

    def settlement(self, c, now, amount='2'):
        later = now + timedelta(seconds=1)
        event = self.financial(c, 4, later, 'funding_settlement', funding_id='late-closed-funding', cost_quote=amount)
        evidence = self.advance(c, later, cash=D('951.358')-D(amount), funding=(Funding('late-closed-funding',D(amount),True),))
        return later, evidence, event

    def test_debit_and_credit_reprice_closed_native_profit_and_wallet_once(self):
        c, trade, other, ex, now = self.closed()
        baseline = trade.close_profit_abs
        closed_at, filled_at = trade.close_date, trade.orders[-1].order_filled_date
        later, evidence, debit = self.settlement(c, now)
        credit = self.financial(c, 5, later, 'funding_settlement', funding_id='late-credit', cost_quote='-.5')
        evidence = replace(evidence, snapshot=replace(evidence.snapshot, balance=D('949.858'),
            funding=evidence.snapshot.funding+(Funding('late-credit',D('-.5'),True),)))
        with c.wallet.clock.at(later):
            result = self.observe_funding(c, 'closed-debit-credit', evidence, [debit,credit])
            again = self.observe_funding(c, 'closed-debit-credit', evidence, [debit,credit])
        self.assertEqual(result['audit'], again['audit'])
        self.assertEqual(D(result['audit']['observed_wallet']), D('949.858'))
        self.assertEqual(D(result['audit']['settled_funding_cash']), D('-1.5'))
        self.assertAlmostEqual(trade.close_profit_abs, baseline-1.5)
        self.assertEqual(trade.funding_fees, -1.5)
        self.assertEqual(trade.orders[-1].funding_fee, -1.5)
        self.assertEqual((trade.close_date, trade.orders[-1].order_filled_date), (closed_at,filled_at))
        self.assertEqual(c.provider.ledger.last_sequence, 5)
        self.assertEqual(len(ex._api.requests), 2)
        self.assertNotIn('_apply_funding', vars(c.runtime))

    def test_open_campaign_funding_keeps_normal_path_in_same_batch(self):
        c, trade, other, ex, now = self.closed()
        later, evidence, event = self.settlement(c, now)
        c.trade = other
        accrued = self.financial(c, 5, later, 'funding_accrual', funding_id='open-funding', cost_quote='.75')
        evidence = replace(evidence, snapshot=replace(evidence.snapshot,
            funding=evidence.snapshot.funding+(Funding('open-funding',D('.75'),False),)))
        with c.wallet.clock.at(later):
            result = self.observe_funding(c, 'mixed-funding', evidence, [event,accrued])
        self.assertEqual(other.funding_fee_running, -.75)
        self.assertEqual(trade.funding_fees, -2)
        self.assertEqual(D(result['audit']['pending_funding_cost']), D('.75'))
        self.assertEqual(D(result['audit']['observed_wallet']), D('949.358'))

    def test_settling_already_accrued_cost_changes_cash_without_new_profit_delta(self):
        c, trade, _, _, now = self.closed()
        later = now+timedelta(seconds=1)
        event = self.financial(c, 4, later, 'funding_accrual', funding_id='accrued-after-close', cost_quote='2')
        evidence = self.advance(c, later, funding=(Funding('accrued-after-close',D(2),False),))
        with c.wallet.clock.at(later):
            accrued = self.observe_funding(c, 'closed-accrual', evidence, [event])
        profit = trade.close_profit_abs
        now = later+timedelta(seconds=1)
        event = self.financial(c, 5, now, 'funding_settlement', funding_id='accrued-after-close', cost_quote='2')
        evidence = self.advance(c, now, cash=D('949.358'), funding=(Funding('accrued-after-close',D(2),True),))
        with c.wallet.clock.at(now):
            settled = self.observe_funding(c, 'closed-accrual-settled', evidence, [event])
        self.assertEqual(trade.close_profit_abs, profit)
        self.assertEqual(trade.funding_fees, -2)
        self.assertEqual(settled['decision'].equity, accrued['decision'].equity)
        self.assertEqual(D(settled['audit']['observed_wallet']), D('949.358'))

    def test_commit_ack_loss_replays_native_target_without_double_charge(self):
        c, trade, _, _, now = self.closed()
        trade_id, baseline = trade.id, trade.close_profit_abs
        later, evidence, event = self.settlement(c, now)
        commit = Trade.commit
        def lose_ack():
            commit()
            raise OSError('synthetic closed funding native commit acknowledgment lost')
        with c.wallet.clock.at(later):
            with patch.object(Trade,'commit',side_effect=lose_ack), self.assertRaisesRegex(OSError,'acknowledgment'):
                self.observe_funding(c, 'closed-funding-fault', evidence, [event])
            Trade.session.remove()
            self.assertEqual(c.gate.s['pending_account_batch'], 'closed-funding-fault')
            with self.assertRaises(RiskError):
                c.wallet.require_entry_clear(later)
            result = self.observe_funding(c, 'closed-funding-fault', evidence, [event])
        native = Trade.session.get(Trade, trade_id)
        self.assertAlmostEqual(native.close_profit_abs, baseline-2)
        self.assertEqual(native.funding_fees, -2)
        self.assertEqual(D(result['audit']['observed_wallet']), D('949.358'))
        self.assertEqual(c.provider.ledger.last_sequence, 4)
        self.assertIsNone(c.gate.s['pending_account_batch'])

    def test_backdated_settlement_cannot_rewrite_accepted_close_checkpoint(self):
        c, trade, _, _, now = self.closed()
        baseline = trade.close_profit_abs
        later, evidence, event = self.settlement(c, now)
        event['event_at'] = now.isoformat()
        with c.wallet.clock.at(later), self.assertRaisesRegex(RiskError,'Late event'):
            self.observe_funding(c, 'backdated-funding', evidence, [event])
        self.assertEqual(trade.close_profit_abs, baseline)
        self.assertEqual(c.provider.ledger.last_sequence, 3)

    def test_precommit_failure_keeps_source_event_and_retries_native_delta(self):
        c, trade, _, _, now = self.closed()
        baseline = trade.close_profit_abs
        later, evidence, event = self.settlement(c, now)
        with c.wallet.clock.at(later):
            with patch.object(Trade,'commit',side_effect=OSError('synthetic before-commit failure')):
                with self.assertRaisesRegex(OSError,'before-commit'):
                    self.observe_funding(c, 'closed-precommit-fault', evidence, [event])
            self.assertEqual(trade.close_profit_abs, baseline)
            self.assertEqual(c.provider.ledger.last_sequence, 4)
            result = self.observe_funding(c, 'closed-precommit-fault', evidence, [event])
        self.assertAlmostEqual(trade.close_profit_abs, baseline-2)
        self.assertEqual(D(result['audit']['settled_funding_cash']), D(-2))
        self.assertEqual(c.provider.ledger.last_sequence, 4)


if __name__ == '__main__':
    unittest.main()
