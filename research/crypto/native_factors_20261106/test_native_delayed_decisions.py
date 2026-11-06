"""Boundary-registered signals, current execution evidence and delayed feedback."""
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE/'sources'))
import test_native_research_driver as dr
from native_research_driver import NativeResearchDriver
from native_research_startup import NativeResearchStartup
from native_factor_entries import fixed_entry_signal, registered_entry_signal, size_entry
from native_strategy_exits import ExitRate
from native_evidence import decimal_text
from freqtrade.persistence import Trade
from rd1 import RiskError, StateError, digest, stamp


class DelayedDecisionTests(unittest.TestCase):
    for _name in ('setUp', 'tearDown', 'provider', 'checkpoint', 'receipts', 'bars', 'setup_runtime',
                  'account', 'prepare_response', 'reports', 'fill_events', 'fixture', 'make', 'ready',
                  'wallet_receipt', 'financial', 'dispatch', 'observe', 'advance', 'enter_two', 'exit_exchange', 'fill_exit'):
        locals()[_name] = getattr(dr.ResearchDriverTests, _name)
    del _name

    def fresh_liquidity(self, c, now):
        return {p:replace(v, at=now, received_at=now) for p,v in c.liquidity.items()}

    def response(self, c, index, now, liquidity):
        pair = c.universe[index]
        inputs = c.driver._frames(now)[0][pair]
        if now == c.now:
            signal, _ = fixed_entry_signal(c.strategy, pair, inputs, now, c.universe)
        else:
            signal, _ = registered_entry_signal(c.gate, c.strategy, pair, inputs, c.now, now, c.universe)
        proposal, _ = size_entry(c.provider, c.wallet._checked_evidence, c.ex, c.wallet.wallets,
            liquidity[pair], signal[1], now)
        self.assertIsNotNone(proposal)
        key = digest([type(c.strategy).__name__, stamp(c.now)])
        rid = 'factor-'+digest([key,pair])[:28]
        client = 'rd1-'+digest(dict(account=c.life.s['identity'], request=rid))[:28]
        raw = dict(symbol=pair.split('/')[0]+'USDT', orderId=100, clientOrderId=client,
            transactTime=int(now.timestamp()*1000), updateTime=int(now.timestamp()*1000),
            price=str(proposal.price), origQty=str(proposal.quantity), executedQty='0',
            cummulativeQuoteQty='0', cumQuote='0', avgPrice='0', status='NEW', timeInForce='GTC', type='LIMIT', side='BUY')
        c.ex._api.responses = [(raw,now)]
        return rid, proposal

    def first(self, name='F2Volatility24Native'):
        c = self.ready(name)
        with c.wallet.clock.at(c.now):
            liquidity = self.fresh_liquidity(c,c.now)
            rid, proposal = self.response(c,0,c.now,liquidity)
            result = self.dispatch(c,c.now,entry_exchange=c.ex,entry_liquidity=liquidity,allow_entry=True)
        self.assertEqual(result['state'],'DISPATCHED_ENTRY')
        self.assertEqual(result['result']['request_id'],rid)
        return c

    def feedback(self,c,now,cursor,*,second_mark=None):
        reports = self.reports(c,now)
        e,receipt,quotes = self.account(c,reports,now,cursor)
        # The old same-time fixture used c.now for every order. Delayed orders
        # retain the creation timestamp actually captured in their own reply.
        created={(item['native_binding']['pair'],item['native_binding']['venue_order_id']):
            datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(milliseconds=item['ccxt_payload']['timestamp'])
            for item in c.gate.s['requests'].values()}
        e=replace(e,snapshot=replace(e.snapshot,orders=tuple(replace(order,
            created_at=created[tuple(e.order_bindings[order.order_id])]) for order in e.snapshot.orders)))
        if second_mark is not None:
            e=replace(e,snapshot=replace(e.snapshot,quotes=tuple(replace(q,price=D(second_mark))
                if q.pair==c.universe[1] else q for q in e.snapshot.quotes)))
            quotes=self.receipts(e)
        with c.wallet.clock.at(now):
            c.driver.observe('delayed-feedback-'+str(cursor),e,receipt,quotes,reports,(),c.bot,now,
                complete_sequence=0,complete_from_inception=True,funding_complete=True)

    def delayed(self,name,count):
        c=self.first(name)
        key=digest([name,stamp(c.now)])
        original_hash=c.gate.s['factor_schedules'][key]['signals_sha256']
        for i in range(1,count):
            now=c.now+timedelta(seconds=i*2)
            self.feedback(c,now,i+1)
            with c.wallet.clock.at(now):
                liquidity=self.fresh_liquidity(c,now)
                rid,proposal=self.response(c,i,now,liquidity)
                result=self.dispatch(c,now,entry_exchange=c.ex,entry_liquidity=liquidity,allow_entry=True)
                self.assertEqual(result['state'],'DISPATCHED_ENTRY')
                self.assertEqual(result['result']['request_id'],rid)
                self.assertEqual(result['entry_timing'],'same_execution_bar_continuation')
                self.assertEqual(self.dispatch(c,now)['state'],'WAIT_ACCOUNT')
            record=c.gate.s['factor_decisions'][rid]
            self.assertEqual(record['decision_at'],stamp(c.now))
            self.assertEqual(record['signal']['at'],stamp(c.now))
            self.assertEqual(record['dispatched_at'],stamp(now))
            self.assertEqual(record['delay_seconds'],i*2)
            trade=Trade.session.get(Trade,c.gate.s['requests'][rid]['native_binding']['trade_id'])
            self.assertEqual(trade.open_date_utc,now)
        self.assertEqual(len(c.ex._api.requests),count)
        self.assertEqual(len(c.gate.s['factor_schedules']),1)
        self.assertEqual(c.gate.s['factor_schedules'][key]['signals_sha256'],original_hash)
        return c

    def test_f1_three_entries_resume_one_registered_signal_set_after_real_time_feedback(self):
        self.delayed('F1Momentum168Native',3)

    def test_f2_entries_resume_original_ema_and_scale_without_retimestamping(self):
        self.delayed('F2Volatility24Native',2)

    def test_late_first_observation_does_not_backfill_boundary_signal(self):
        c=self.ready();now=c.now+timedelta(seconds=2)
        with c.wallet.clock.at(now):
            self.observe(c,'late-first',self.advance(c,now))
            result=self.dispatch(c,now,entry_exchange=c.ex,entry_liquidity=self.fresh_liquidity(c,now),allow_entry=True)
        self.assertEqual(result['state'],'IDLE')
        self.assertEqual(result['entry_timing'],'no_active_boundary_registration')
        self.assertFalse(c.gate.s.get('factor_schedules'))
        self.assertEqual(c.ex._api.requests,[])

    def test_delayed_entry_sizes_against_current_mark_and_execution_ask(self):
        c=self.first();now=c.now+timedelta(seconds=2)
        self.feedback(c,now,2,second_mark='11')
        with c.wallet.clock.at(now):
            liquidity=self.fresh_liquidity(c,now)
            pair=c.universe[1];liquidity[pair]=replace(liquidity[pair],bid='10.01',ask='10.01')
            rid,proposal=self.response(c,1,now,liquidity)
            result=self.dispatch(c,now,entry_exchange=c.ex,entry_liquidity=liquidity,allow_entry=True)
        self.assertEqual(result['state'],'DISPATCHED_ENTRY')
        self.assertEqual(proposal.quantity,D('8.80'))
        self.assertEqual(proposal.price,D('10.01'))
        self.assertEqual(c.gate.s['factor_decisions'][rid]['sizing']['valuation_price'],'11')

    def test_original_signal_never_reuses_stale_liquidity_permission(self):
        c=self.first();now=c.now+timedelta(seconds=16)
        self.feedback(c,now,2)
        with c.wallet.clock.at(now):
            self.response(c,1,now,self.fresh_liquidity(c,now))
            with self.assertRaisesRegex(RiskError,'liquidity/rules/scale/cost'):
                self.dispatch(c,now,entry_exchange=c.ex,entry_liquidity=c.liquidity,allow_entry=True)
        self.assertEqual(len(c.ex._api.requests),1)

    def test_changed_signal_prefix_cannot_replace_the_registered_boundary(self):
        c=self.first();now=c.now+timedelta(seconds=2)
        self.feedback(c,now,2)
        with c.wallet.clock.at(now):
            inputs=c.driver._frames(now)[0]
            pair=c.universe[1];main=inputs[pair].main.copy()
            main.loc[main.index[-1],['close','high']]=[21.,22.]
            inputs=dict(inputs);inputs[pair]=replace(inputs[pair],main=main)
            with c.driver._scope(now,'dispatch'):
                with self.assertRaisesRegex(StateError,'prefix differs'):
                    c.runtime.entry_step(c.strategy,inputs,self.fresh_liquidity(c,now),c.ex,now,decision_at=c.now)
        self.assertEqual(len(c.ex._api.requests),1)

    def test_completed_startup_reinitialization_retains_original_pending_schedule(self):
        c=self.first();now=c.now+timedelta(seconds=2)
        self.feedback(c,now,2)
        c.driver.close();c.startup.close()
        c.startup=NativeResearchStartup(self.root/'startup',c.gate,c.wallet,c.strategy,c.frames,'synthetic-startup-bars')
        self.opened.append(c.startup)
        with c.wallet.clock.at(now):c.startup.run(now)
        c.driver=NativeResearchDriver(c.startup,c.loop);self.opened.append(c.driver)
        with c.wallet.clock.at(now):
            liquidity=self.fresh_liquidity(c,now);rid,_=self.response(c,1,now,liquidity)
            result=self.dispatch(c,now,entry_exchange=c.ex,entry_liquidity=liquidity,allow_entry=True)
        self.assertEqual(result['result']['request_id'],rid)
        self.assertEqual(len(c.gate.s['factor_schedules']),1)
        self.assertEqual(len(c.ex._api.requests),2)

    def test_old_signal_expires_at_next_execution_bar_without_new_orders(self):
        c=self.ready();self.enter_two(c)
        now=c.now+timedelta(minutes=5)
        e=self.advance(c,now)
        adapters={};rates={}
        for trade in Trade.get_trades_proxy():
            ex,_,_=self.exit_exchange(c,trade,now,'1');ex._api.responses=[]
            adapters[trade.pair]=ex
            rates[trade.pair]=ExitRate(trade.pair,'10',now,now,'synthetic-expiry-bid','synthetic')
        with c.wallet.clock.at(now):
            self.observe(c,'expired-signal',e)
            result=self.dispatch(c,now,exit_exchanges=adapters,exit_rates=rates,
                entry_exchange=c.ex,entry_liquidity=self.fresh_liquidity(c,now),allow_entry=True)
        self.assertEqual(result['state'],'IDLE')
        self.assertEqual(result['entry_timing'],'no_active_boundary_registration')
        self.assertEqual(len(c.gate.s['factor_schedules']),1)
        self.assertEqual(len(c.ex._api.requests),2)
        self.assertTrue(all(ex._api.requests==[] for ex in adapters.values()))

    def test_f2_boundary_targets_survive_priority_stop_before_other_pair_reduction(self):
        c=self.ready();self.enter_two(c)
        first,second=Trade.get_trades_proxy()
        midnight=(c.now+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
        with c.wallet.clock.at(midnight):self.observe(c,'target-midnight',self.advance(c,midnight))
        now=midnight+timedelta(minutes=5)
        with c.wallet.clock.at(now):
            self.observe(c,'target-boundary',self.advance(c,now,marks={first.pair:D('9.4')}))
        ex,raw,aid=self.exit_exchange(c,first,now,D('9.68'),price='9.4',kind='STRATEGY_EXIT')
        with c.wallet.clock.at(now):
            # The 00:05 UTC decision remains the same instant when supplied in
            # the user's local timezone; do not inspect local hour/minute.
            result=self.dispatch(c,now.astimezone(timezone(timedelta(hours=8))),exit_exchanges={first.pair:ex},
                exit_rates={first.pair:ExitRate(first.pair,'9.4',now,now,'synthetic-priority-bid','synthetic')})
            self.assertEqual(result['result'],aid)
        daily=c.exits.s['daily_position_targets']
        self.assertEqual(len(daily),2)
        key=f'F2Volatility24Native:{second.id}:{now.date().isoformat()}'
        frozen=copy.deepcopy(daily[key])
        self.assertEqual(frozen['decision_at'],stamp(now))
        now+=timedelta(seconds=1)
        self.fill_exit(c,first,ex,raw,aid,now)
        target=D(decimal_text(c.ex.amount_to_contract_precision(second.pair,float(D(frozen['max_remaining_quantity'])))))
        ex,raw,aid=self.exit_exchange(c,second,now,D('9.68')-target,venue=201)
        with c.wallet.clock.at(now):
            result=self.dispatch(c,now,exit_exchanges={second.pair:ex},
                exit_rates={second.pair:ExitRate(second.pair,'10',now,now,'synthetic-delayed-reduction-bid','synthetic')})
        self.assertEqual(result['result'],aid)
        self.assertEqual(second.exit_reason,'F2_daily_scale')
        self.assertEqual(c.exits.s['daily_position_targets'][key],frozen)
        self.assertEqual(c.exits.s['actions'][aid]['target_remaining_quantity'],str(target))
        self.assertEqual(len(c.exits.s['daily_target_registrations']),1)


if __name__=='__main__':unittest.main()
