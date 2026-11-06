"""Registered data -> fixed F2 native orders -> complete synthetic account flow."""
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE/'sources'))
import test_native_research_startup as su
import test_native_account_runtime as ar
from native_research_driver import NativeResearchDriver
from native_research_startup import NativeResearchStartup
from native_factor_loop import NativeFactorLoop
from native_cancellations import NativeCancellation
from native_exits import NativeRiskExit, LocalExitExchange
from native_entries import LocalBinanceTape
from native_strategy_exits import ExitRate
from native_position_targets import build_position_targets
from native_evidence import decimal_text
from native_protections import locks
from freqtrade.persistence import Trade
from rd1 import Funding, RiskError, StateError, digest


class ResearchDriverTests(unittest.TestCase):
    for _name in ('setUp', 'tearDown', 'provider', 'checkpoint', 'receipts', 'bars', 'setup_runtime',
                  'account', 'prepare_response', 'reports', 'fill_events', 'fixture', 'make'):
        locals()[_name] = getattr(su.ResearchStartupTests, _name)
    del _name
    wallet_receipt = ar.AccountRuntimeTests.wallet_receipt
    financial = ar.AccountRuntimeTests.financial

    def ready(self, name='F2Volatility24Native'):
        c = self.fixture('futures', name)
        day = pd.Timestamp(c.now).normalize()
        if name == 'F2Volatility24Native':
            dates = pd.date_range(end=day-pd.Timedelta(hours=4), periods=40, freq='4h')
            future = pd.date_range(start=day, periods=8, freq='4h')
            hourly_dates = pd.date_range(end=day+pd.Timedelta(hours=24), periods=194, freq='h')
            # Deterministic fixture, not candidate/parameter search: low past
            # volatility then one full day of higher alternating hourly moves.
            past = 10*np.exp(.0001*np.sin(np.arange(169)))
            following = past[-1]*np.exp(np.cumsum(np.array([.01, -.01]*13)[:25]))
            hourly = self.bars(hourly_dates, np.r_[past, following])
            for i, pair in enumerate(c.universe):
                past_close = np.r_[np.linspace(12, 9, 39), 20.] if i < 2 else np.full(40, 10.)
                main = self.bars(dates.append(future), np.r_[past_close, np.full(8, past_close[-1])])
                c.frames[(pair, '4h')] = main
                c.frames[(pair, '1h')] = hourly
        c.startup = self.make(c)
        c.cancel = NativeCancellation(self.root/'driver-cancel', c.gate, create=True)
        c.exits = NativeRiskExit(self.root/'driver-exits', c.gate, c.wallet, create=True)
        self.opened.extend([c.cancel, c.exits])
        c.loop = NativeFactorLoop(c.runtime, c.cancel, c.exits)
        with c.wallet.clock.at(c.now):
            c.startup.run(c.now)
        c.driver = NativeResearchDriver(c.startup, c.loop)
        self.opened.append(c.driver)
        return c

    def dispatch(self, c, now, **overrides):
        args = dict(cancel_exchanges={}, exit_exchanges={}, exit_rates={}, allow_entry=False)
        args.update(overrides)
        return c.driver.dispatch(c.bot, now, **args)

    def observe(self, c, label, evidence, events=(), reports=None):
        return c.driver.observe(label, evidence, self.wallet_receipt(evidence), self.receipts(evidence),
            reports or {}, events, c.bot, evidence.snapshot.at,
            complete_sequence=max([c.provider.ledger.last_sequence]+[e['sequence'] for e in events]),
            complete_from_inception=True, funding_complete=True)

    def advance(self, c, now, *, cash=None, funding=None, quantities=None, marks=None):
        e = c.wallet._checked_evidence
        positions = e.snapshot.positions
        if quantities is not None:
            positions = tuple(replace(p, quantity=quantities.get(p.pair, p.quantity),
                collateral=quantities.get(p.pair, p.quantity)*p.entry_price)
                for p in positions if quantities.get(p.pair, p.quantity) > 0)
        kept = {p.position_id for p in positions}
        quotes = tuple(replace(q, at=now, price=(marks or {}).get(q.pair, q.price)) for q in e.snapshot.quotes)
        snap = replace(e.snapshot, at=now, balance_at=now, orders_at=now, cursor=e.snapshot.cursor+1,
            balance=e.snapshot.balance if cash is None else cash, positions=positions, quotes=quotes,
            funding=e.snapshot.funding if funding is None else funding, orders=())
        return replace(e, snapshot=snap, position_bindings={p:t for p,t in e.position_bindings.items() if p in kept},
            order_bindings={}, order_envelopes=(), partial_base_fees={})

    def enter_two(self, c):
        with c.wallet.clock.at(c.now):
            for i in range(2):
                c.inputs = c.driver._frames(c.now)[0]
                rid = self.prepare_response(c, i)
                result = self.dispatch(c, c.now, entry_exchange=c.ex, entry_liquidity=c.liquidity, allow_entry=True)
                self.assertEqual(result['state'], 'DISPATCHED_ENTRY')
                self.assertEqual(result['result']['request_id'], rid)
                self.assertEqual(self.dispatch(c, c.now)['state'], 'WAIT_ACCOUNT')
                reports = self.reports(c, c.now)
                e, receipt, quotes = self.account(c, reports, c.now, i+2)
                c.driver.observe('driver-open-'+str(i), e, receipt, quotes, reports, (), c.bot, c.now,
                    complete_sequence=0, complete_from_inception=True, funding_complete=True)
        now = c.now+timedelta(seconds=1)
        reports = self.reports(c, now, fill=100)
        events = self.fill_events(c, reports, now)
        e, receipt, quotes = self.account(c, reports, now, 4)
        with c.wallet.clock.at(now):
            result = c.driver.observe('driver-filled', e, receipt, quotes, reports, events, c.bot, now,
                complete_sequence=2, complete_from_inception=True, funding_complete=True)
        self.assertEqual(len(result['evidence'].snapshot.positions), 2)
        self.assertEqual([t.amount for t in Trade.get_trades_proxy()], [9.68, 9.68])
        self.assertEqual(D(result['audit']['fees_paid']), D('.1936'))
        return now

    def exit_exchange(self, c, trade, now, quantity, *, price='10', kind='POSITION_REDUCTION', venue=200, action_id=None):
        aid = action_id or digest(dict(account=c.life.risk.s['identity'], trade_id=trade.id, kind=kind))[:24]
        key = dict(account=c.life.risk.s['identity'], action=aid)
        raw = dict(symbol=trade.pair.split('/')[0]+'USDT', orderId=venue,
            clientOrderId='rd1x-'+digest(key)[:28], time=int(now.timestamp()*1000), transactTime=int(now.timestamp()*1000),
            updateTime=int(now.timestamp()*1000), price=price, origQty=str(quantity), executedQty='0',
            cummulativeQuoteQty='0', cumQuote='0', avgPrice='0', status='NEW', timeInForce='GTC', type='LIMIT', side='SELL', reduceOnly=True)
        tape = LocalBinanceTape([c.ex.markets[trade.pair]], [(raw, now)], allowed_methods=('POST', 'GET'))
        ex = LocalExitExchange(tape, c.exits)
        self.opened.append(ex)
        return ex, raw, aid

    def fill_exit(self, c, trade, ex, raw, aid, now, *, fault=False):
        quantity, price = D(raw['origQty']), D(raw['price'])
        before = c.wallet._checked_evidence
        original = next(p for p in before.snapshot.positions if p.pair == trade.pair).quantity
        raw = copy.deepcopy(raw)
        raw.update(status='FILLED', executedQty=str(quantity), cummulativeQuoteQty=str(quantity*price),
            cumQuote=str(quantity*price), avgPrice=str(price), updateTime=int(now.timestamp()*1000))
        ex._api.responses = [(raw, now)]
        c.trade = trade
        event = self.financial(c, c.provider.ledger.last_sequence+1, now, 'fill', order_id=str(raw['orderId']),
            fill_id='driver-fill-'+str(raw['orderId']), signed_quantity=str(-quantity), price=str(price),
            fee_quote=str(quantity*price*D('.001')), fee_base='0')
        cash = before.snapshot.balance + quantity*(price-10) - quantity*price*D('.001')
        e = self.advance(c, now, cash=cash, quantities={trade.pair:original-quantity})
        label = 'driver-exit-'+str(raw['orderId'])
        with c.wallet.clock.at(now):
            ex.query(aid, now)
            if fault:
                with patch.object(c.provider, 'checkpoint', side_effect=OSError('synthetic account checkpoint interruption')):
                    with self.assertRaises(OSError): self.observe(c, label, e, [event])
                count = len(locks())
                self.assertGreater(count, 0)
                with patch.object(c.bot.strategy, 'order_filled', side_effect=AssertionError('fill callback repeated')):
                    result = self.observe(c, label, e, [event])
                self.assertEqual(len(locks()), count)
            else:
                result = self.observe(c, label, e, [event])
        return result

    def test_registered_provider_drives_f2_multicoin_funding_daily_reductions_and_protected_stop(self):
        c = self.ready(); now = self.enter_two(c)
        first, second = Trade.get_trades_proxy()
        now += timedelta(seconds=1)
        c.trade = first
        debit = self.financial(c, 3, now, 'funding_settlement', funding_id='driver-debit', cost_quote='2')
        c.trade = second
        credit = self.financial(c, 4, now, 'funding_settlement', funding_id='driver-credit', cost_quote='-.5')
        funds = (Funding('driver-debit', D(2), True), Funding('driver-credit', D('-.5'), True))
        e = self.advance(c, now, cash=D('998.3064'), funding=funds)
        with c.wallet.clock.at(now):
            funded = self.observe(c, 'driver-funded', e, [debit, credit])
        self.assertEqual(D(funded['decision'].equity), D('998.15152'))
        self.assertEqual([first.funding_fees, second.funding_fees], [-2, .5])
        # Explicit next-day full account anchor is indispensable; not a carry
        # forward value manufactured by the driver.
        midnight = (c.now+timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        e = self.advance(c, midnight)
        with c.wallet.clock.at(midnight): self.observe(c, 'driver-midnight', e)
        now = midnight+timedelta(minutes=5)
        e = self.advance(c, now)
        with c.wallet.clock.at(now):
            self.observe(c, 'driver-daily', e)
            _, _, reduction, _ = c.driver._frames(now)
            targets, _, _ = build_position_targets(c.provider, c.wallet._checked_evidence, c.strategy, reduction, now, {})
        retained = {}
        for i, trade in enumerate((first, second)):
            target = D(decimal_text(c.ex.amount_to_contract_precision(trade.pair, float(targets[trade.pair]))))
            retained[trade.pair] = target
            quantity = D(str(trade.amount))-target
            ex, raw, aid = self.exit_exchange(c, trade, now, quantity, venue=200+i)
            adapters = {trade.pair:ex}
            # Both checked positions are evaluated in frozen order; after the
            # first reduction its adapter remains available for the no-op check.
            if i: adapters[first.pair] = first_ex
            rates = {p:ExitRate(p, '10', now, now, 'synthetic-driver-bid', 'synthetic') for p in adapters}
            with c.wallet.clock.at(now):
                result = self.dispatch(c, now, exit_exchanges=adapters, exit_rates=rates)
                self.assertEqual(result['state'], 'DISPATCHED_EXIT')
                self.assertEqual(result['result'], aid)
                self.assertEqual(trade.exit_reason, 'F2_daily_scale')
            now += timedelta(seconds=1)
            reduced = self.fill_exit(c, trade, ex, raw, aid, now)
            self.assertEqual(D(str(trade.amount)), target)
            if i == 0: first_ex = ex
        self.assertEqual(len(c.exits.s['daily_position_targets']), 2)
        self.assertEqual(retained, {first.pair:D('2.02'), second.pair:D('2.02')})
        self.assertEqual(D(reduced['decision'].equity), D('998.12088'))
        self.assertEqual([first.orders[-1].funding_fee, second.orders[-1].funding_fee], [-2, .5])
        # The first leg then stops while the second remains open. Full account
        # NAV retains the second leg and its hypothetical exit cost reserve.
        now += timedelta(seconds=1)
        e = self.advance(c, now, marks={first.pair:D('9.4')})
        with c.wallet.clock.at(now): self.observe(c, 'driver-stop-price', e)
        ex, raw, aid = self.exit_exchange(c, first, now, D('2.02'), price='9.4', kind='STRATEGY_EXIT', venue=202)
        rate = ExitRate(first.pair, '9.4', now, now, 'synthetic-driver-stop-bid', 'synthetic')
        with c.wallet.clock.at(now):
            result = self.dispatch(c, now, exit_exchanges={first.pair:ex}, exit_rates={first.pair:rate})
            self.assertEqual(result['result'], aid)
            self.assertEqual(first.exit_reason, 'stop_loss')
        now += timedelta(seconds=1)
        final = self.fill_exit(c, first, ex, raw, aid, now, fault=True)
        self.assertFalse(first.is_open); self.assertTrue(second.is_open)
        self.assertEqual(len(final['evidence'].snapshot.positions), 1)
        self.assertEqual(D(final['audit']['settled_funding_cash']), D('-1.5'))
        self.assertEqual(D(final['audit']['fees_paid']), D('.365788'))
        self.assertEqual(D(final['decision'].equity), D('996.906052'))
        self.assertEqual(final['evidence'].snapshot.balance, D('996.922212'))
        self.assertAlmostEqual(first.close_profit_abs, -3.404388, places=8)
        self.assertEqual([o.funding_fee for o in first.orders], [0, -2, 0])
        self.assertEqual(D(str(second.amount)), D('2.02'))
        with c.wallet.clock.at(now):
            self.assertFalse(c.startup.protections.entry_allowed(first.pair, now))
            self.assertTrue(c.startup.protections.entry_allowed(second.pair, now))
            self.assertEqual(c.startup.protections.timeframe, '4h')
        self.assertEqual(len(c.ex._api.requests), 2)

    def test_f1_uses_registered_native_closed_prefix(self):
        c = self.ready('F1Momentum168Native')
        with c.wallet.clock.at(c.now):
            entries, _, _, proof = c.driver._frames(c.now)
            first = c.universe[0]
            self.assertEqual(entries[first].main.date.max(), pd.Timestamp(c.now)-pd.Timedelta(minutes=5))
            self.assertEqual(proof['hourly'][first]['rows'], 169)
            c.inputs = entries; self.prepare_response(c, 0)
            result = self.dispatch(c, c.now, entry_exchange=c.ex, entry_liquidity=c.liquidity, allow_entry=True)
            self.assertEqual(result['state'], 'DISPATCHED_ENTRY')
            record = c.gate.s['research_driver_frames']['1']
            self.assertEqual(record['data']['data_hash'], c.startup.binding['data_hash'])

    def test_registered_driver_rejects_bypass_and_closed_or_missing_owner(self):
        c = self.ready()
        with c.wallet.clock.at(c.now):
            with self.assertRaisesRegex(StateError, 'driver must own'):
                c.loop.dispatch(c.bot, c.now, cancel_exchanges={}, exit_exchanges={}, exit_rates={}, strategy_inputs={}, reduction_inputs=None)
            with self.assertRaisesRegex(StateError, 'driver must own'):
                c.runtime.entry_step(c.strategy, {}, {}, c.ex, c.now)
            with self.assertRaisesRegex(StateError, 'driver must own'):
                c.gate.submit(None, 'bypass', None, None, c.now)
            with self.assertRaisesRegex(StateError, 'driver must own'):
                c.exits.dispatch(None, c.wallet._checked_evidence, None, c.bot, c.now, limit='10')
            with self.assertRaisesRegex(StateError, 'driver must own'):
                c.cancel.dispatch('bypass', c.wallet._checked_evidence, None, c.now)
            with self.assertRaisesRegex(StateError, 'driver must own'):
                c.runtime.observe_account('bypass', None, None, (), {}, (), c.bot, c.now,
                    complete_sequence=0, complete_from_inception=True, funding_complete=True)
            with patch.object(c.gate, 'research_driver', None):
                with self.assertRaisesRegex(StateError, 'binding unavailable'): self.dispatch(c, c.now)
            c.driver.close()
            with self.assertRaisesRegex(StateError, 'binding unavailable'): self.dispatch(c, c.now)
        self.assertEqual(c.ex._api.requests, [])

    def test_corrupt_registered_data_blocks_flat_account_without_request(self):
        c = self.ready()
        key = (c.universe[0], '1h')
        c.startup.dp.frames[key].loc[0, 'close'] *= 2
        with c.wallet.clock.at(c.now):
            result = self.dispatch(c, c.now, entry_exchange=c.ex, entry_liquidity=c.liquidity, allow_entry=True)
        self.assertEqual(result['state'], 'DATA_BLOCKED')
        self.assertTrue(result['data_errors'])
        self.assertEqual(c.ex._api.requests, [])

    def test_incomplete_account_observation_can_retry_through_driver(self):
        c = self.ready()
        e, receipt, quotes = self.account(c, {}, c.now, 2)
        with c.wallet.clock.at(c.now):
            with patch.object(c.provider, 'checkpoint', side_effect=OSError('synthetic bootstrap interruption')):
                with self.assertRaises(OSError):
                    c.driver.observe('retry-driver', e, receipt, quotes, {}, (), c.bot, c.now,
                        complete_sequence=0, complete_from_inception=True, funding_complete=True)
            with self.assertRaises(RiskError): self.dispatch(c, c.now)
            result = c.driver.observe('retry-driver', e, receipt, quotes, {}, (), c.bot, c.now,
                complete_sequence=0, complete_from_inception=True, funding_complete=True)
            self.assertEqual(result['evidence'].snapshot.cursor, 2)

    def test_registered_driver_restart_reattaches_same_data_and_controllers(self):
        c = self.ready()
        with c.wallet.clock.at(c.now):
            self.assertEqual(self.dispatch(c, c.now)['state'], 'IDLE')
        original = copy.deepcopy(c.driver.binding)
        c.driver.close(); c.startup.close()
        startup = NativeResearchStartup(self.root/'startup', c.gate, c.wallet, c.strategy,
            c.frames, 'synthetic-startup-bars')
        self.opened.append(startup)
        with c.wallet.clock.at(c.now): startup.run(c.now)
        driver = NativeResearchDriver(startup, c.loop)
        self.opened.append(driver)
        c.startup, c.driver = startup, driver
        self.assertEqual(driver.binding, original)
        with c.wallet.clock.at(c.now):
            self.assertEqual(self.dispatch(c, c.now)['state'], 'IDLE')
        self.assertEqual(len(c.gate.s['research_driver_frames']), 1)

    def test_data_identity_cannot_change_within_an_account_cursor(self):
        c = self.ready()
        with c.wallet.clock.at(c.now):
            self.assertEqual(self.dispatch(c, c.now)['state'], 'IDLE')
            c.startup.dp.frames[(c.universe[0], '1h')].loc[0, 'close'] *= 2
            with self.assertRaisesRegex(StateError, 'data changed within'):
                self.dispatch(c, c.now)
        self.assertEqual(c.ex._api.requests, [])

    def test_native_stop_survives_corrupt_factor_data_without_new_risk(self):
        c = self.ready(); now = self.enter_two(c)+timedelta(seconds=1)
        trade = Trade.get_trades_proxy()[0]
        e = self.advance(c, now, marks={trade.pair:D('9.4')})
        with c.wallet.clock.at(now): self.observe(c, 'driver-bad-data-price', e)
        c.startup.dp.frames[(trade.pair, '1h')].loc[0, 'close'] *= 2
        ex, raw, aid = self.exit_exchange(c, trade, now, D('9.68'), price='9.4', kind='STRATEGY_EXIT')
        rate = ExitRate(trade.pair, '9.4', now, now, 'synthetic-driver-bid', 'synthetic')
        with c.wallet.clock.at(now):
            result = self.dispatch(c, now, exit_exchanges={trade.pair:ex}, exit_rates={trade.pair:rate}, allow_entry=True)
        self.assertEqual(result['state'], 'DISPATCHED_EXIT')
        self.assertEqual(result['result'], aid)
        self.assertTrue(result['data_errors'])
        self.assertEqual(trade.exit_reason, 'stop_loss')
        self.assertEqual(len(c.ex._api.requests), 2)

    def test_urgent_rd1_exit_does_not_read_factor_data(self):
        c = self.ready(); now = self.enter_two(c)+timedelta(seconds=1)
        trade = Trade.get_trades_proxy()[0]
        e = self.advance(c, now, marks={trade.pair:D('5')})
        with c.wallet.clock.at(now):
            self.observe(c, 'driver-urgent-price', e)
            action = next(a for a in c.life.risk.evaluate(c.wallet._checked_evidence.snapshot, now).actions
                if a['kind'] == 'EXIT_POSITION' and a['target'] == 'campaign-'+str(trade.id))
        ex, raw, aid = self.exit_exchange(c, trade, now, D('9.68'), price='5', action_id=action['id'])
        rate = ExitRate(trade.pair, '5', now, now, 'synthetic-driver-risk-bid', 'synthetic')
        with c.wallet.clock.at(now):
            with patch.object(c.driver, '_frames', side_effect=AssertionError('risk exit read signal data')):
                result = self.dispatch(c, now, exit_exchanges={trade.pair:ex}, exit_rates={trade.pair:rate})
        self.assertEqual(result['state'], 'DISPATCHED_EXIT')
        self.assertEqual(result['result'], aid)
        self.assertEqual(c.gate.s['research_driver_frames'][str(e.snapshot.cursor)]['data']['mode'],
            'risk_first_without_signal_read')


if __name__ == '__main__':
    unittest.main()
