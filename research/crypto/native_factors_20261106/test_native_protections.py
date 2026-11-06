"""Actual frozen ProtectionManager/PairLocks, closed fills and entry barriers."""
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'sources'))
import test_native_account_runtime as ar
import test_native_factor_runtime as mf
from test_native_factors import resolved
from native_protections import NativeProtections,locks
from native_entries import NativeEntryGate
from freqtrade.persistence import Trade,PairLocks
from freqtrade.persistence.models import PairLock
from freqtrade.enums import TradingMode
from rd1 import RiskError,StateError


class NativeProtectionTests(unittest.TestCase):
    for _name in ('setUp','tearDown','provider','checkpoint','receipts','event','account','execute','receipt','boot','harness','ready',
                  'wallet_receipt','evidence','financial','observe','dispatch','terminal'):
        locals()[_name]=getattr(ar.AccountRuntimeTests,_name)
    del _name

    def attach(self,c,timeframe='5m'):
        if timeframe=='4h':c.bot.strategy=resolved('F2Volatility24Native',(c.trade.pair,))
        p=NativeProtections(self.root/'native-protections',c.gate,c.wallet,c.bot.strategy,create=True);self.opened.append(p)
        return p

    def closed(self,timeframe='5m',fault=False):
        c=self.ready('spot');p=self.attach(c,timeframe);now=c.now+timedelta(seconds=1)
        e=self.evidence(c,now,3,cash='949.95',mark='5')
        with c.wallet.clock.at(now):
            checked=self.observe(c,'drawdown',e)['evidence'];aid,ex,raw=self.dispatch(c,checked,now)
        now+=timedelta(seconds=1)
        with c.wallet.clock.at(now):
            event=self.terminal(c,aid,ex,raw,now,'5')
            e=self.evidence(c,now,4,cash='974.925',quantity='0',mark='5')
            if fault:
                save=p.store.save
                def lost(state):
                    save(state)
                    if any(v['state']=='APPLIED' for v in state['events'].values()):raise OSError('synthetic native protection acknowledgement lost')
                with patch.object(p.store,'save',side_effect=lost):
                    with self.assertRaises(StateError):self.observe(c,'protected-close',e,[event],[aid])
                self.assertFalse(c.trade.is_open);count=len(locks());p.close()
                p=NativeProtections(self.root/'native-protections',c.gate,c.wallet,c.bot.strategy);self.opened.append(p)
                with patch.object(p.manager,'stop_per_pair',side_effect=AssertionError('native protection evaluation repeated')):
                    result=self.observe(c,'protected-close',e,[event],[aid])
                self.assertEqual(len(locks()),count)
            else:result=self.observe(c,'protected-close',e,[event],[aid])
            c.guard.require_entry_clear()
            with self.assertRaisesRegex(RiskError,'protection lock'):p.require_entry(c.trade.pair,now)
        self.assertEqual(D(result['decision'].equity),D('974.925'))
        self.assertEqual(p.manager.name_list,['CooldownPeriod','StoplossGuard','MaxDrawdown'])
        record=next(iter(p.s['events'].values()));self.assertEqual(record['state'],'APPLIED')
        self.assertAlmostEqual(float(record['input']['starting_balance']),990)
        self.assertEqual(len(locks()),2)  # Native one-candle auto-lock plus frozen cooldown.
        return c,p,now

    def test_native_close_applies_cooldown_and_5m_rounding(self):
        c,p,now=self.closed();end=now.replace(minute=5,second=0,microsecond=0)+timedelta(hours=1)
        self.assertEqual(max(x.lock_end_time_utc for x in PairLock.session.query(PairLock).all()),end)
        with c.wallet.clock.at(end-timedelta(seconds=1)):self.assertFalse(p.entry_allowed(c.trade.pair,end-timedelta(seconds=1)))
        with c.wallet.clock.at(end):self.assertTrue(p.entry_allowed(c.trade.pair,end))

    def test_f2_native_pairlocks_round_to_4h_primary_not_5m_detail(self):
        c,p,now=self.closed('4h');end=now.replace(hour=16,minute=0,second=0,microsecond=0)
        self.assertEqual(max(x.lock_end_time_utc for x in PairLock.session.query(PairLock).all()),end)
        with c.wallet.clock.at(end):self.assertTrue(p.entry_allowed(c.trade.pair,end))

    def test_committed_manager_ack_loss_reopens_without_callbacks_or_duplicate_locks(self):self.closed(fault=True)

    def test_native_lock_or_closed_profit_tampering_blocks_new_entry(self):
        c,p,now=self.closed();c.trade.close_profit_abs-=1;Trade.commit()
        with c.wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError,'history changed'):p.require_entry(c.trade.pair,now)

    def test_changed_persisted_native_lock_blocks_new_entry(self):
        c,p,now=self.closed();lock=PairLock.session.query(PairLock).first();lock.active=False;Trade.commit()
        with c.wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError,'locks changed'):p.require_entry(c.trade.pair,now)

    def test_partial_manager_failure_never_certifies_closed_trade_or_repeats_callbacks(self):
        c=self.ready('spot');p=self.attach(c);now=c.now+timedelta(seconds=1)
        e=self.evidence(c,now,3,cash='949.95',mark='5')
        with c.wallet.clock.at(now):
            checked=self.observe(c,'drawdown',e)['evidence'];aid,ex,raw=self.dispatch(c,checked,now)
        now+=timedelta(seconds=1)
        with c.wallet.clock.at(now):
            event=self.terminal(c,aid,ex,raw,now,'5');e=self.evidence(c,now,4,cash='974.925',quantity='0',mark='5')
            with patch.object(p.manager,'global_stop',side_effect=OSError('synthetic native protection handler failure')):
                with self.assertRaises(OSError):self.observe(c,'failed-protection',e,[event],[aid])
            self.assertFalse(c.trade.is_open);self.assertEqual(next(iter(p.s['events'].values()))['state'],'PREPARED')
            with self.assertRaises(RiskError):p.require_entry(c.trade.pair,now)
            with patch.object(c.bot.strategy,'order_filled',side_effect=AssertionError('native fill callback repeated')):
                with self.assertRaisesRegex(RiskError,'protection effects are unverified'):
                    self.observe(c,'failed-protection',e,[event],[aid])
            with self.assertRaises(RiskError):c.gate.require_account_batch_clear()

    def test_registered_manager_cannot_be_forgotten_when_entry_gate_reopens(self):
        c=self.ready('spot');p=self.attach(c);c.gate.close()
        gate=NativeEntryGate(self.root/'spot-receipts',c.gate.lifecycle,after_fill_guard=c.guard);self.opened.append(gate);c.wallet.attach(gate)
        with c.wallet.clock.at(c.now):
            with self.assertRaisesRegex(StateError,'protections must be reattached'):
                gate.submit(None,'next',c.e,SimpleNamespace(pair=c.trade.pair),c.now)

    def test_changed_primary_timeframe_or_handler_parameters_rejected(self):
        c=self.ready('spot');p=self.attach(c)
        with c.wallet.clock.at(c.now):
            with patch.object(PairLocks,'timeframe','4h'):
                with self.assertRaisesRegex(RiskError,'configuration/database'):p.require_entry(c.trade.pair,c.now)
            p.manager._protection_handlers[0]._protection_config['stop_duration']=1
            with self.assertRaisesRegex(RiskError,'configuration/database'):p.require_entry(c.trade.pair,c.now)

    def closed_history(self,c,count,reason):
        # Explicit component fixtures for native plugins, not source-account runs.
        for i in range(count):
            trade=Trade(pair=c.trade.pair,exchange='binance',is_open=False,open_date=c.now-timedelta(hours=2),
                close_date=c.now-timedelta(seconds=count-i),open_rate=10,close_rate=9,amount=10,stake_amount=100,
                fee_open=0,fee_close=0,leverage=1,trading_mode=TradingMode.SPOT,exit_reason=reason,close_profit=-.1,close_profit_abs=-10)
            Trade.session.add(trade)
        Trade.commit()

    def test_official_stoploss_guard_needs_four_losing_stops_and_blocks_other_pairs(self):
        c=self.ready('spot');p=self.attach(c);self.closed_history(c,3,'stop_loss')
        with c.wallet.clock.at(c.now):
            self.assertIsNone(p.manager.global_stop(now=c.now,starting_balance=1000))
            self.closed_history(c,1,'stop_loss')
            lock=p.manager.global_stop(now=c.now,starting_balance=1000)
            self.assertEqual(lock.pair,'*');self.assertTrue(PairLocks.is_pair_locked('ETH/USDT',c.now,side='long'))
            self.assertGreaterEqual(lock.lock_end_time_utc,c.now+timedelta(minutes=120))

    def test_official_equity_drawdown_needs_ten_closed_trades(self):
        c=self.ready('spot');p=self.attach(c);self.closed_history(c,9,'exit_signal')
        with c.wallet.clock.at(c.now):
            self.assertIsNone(p.manager.global_stop(now=c.now,starting_balance=1000))
            self.closed_history(c,1,'exit_signal')
            lock=p.manager.global_stop(now=c.now,starting_balance=1000)
            self.assertEqual(lock.pair,'*');self.assertIn('0.08',lock.reason)
            self.assertGreaterEqual(lock.lock_end_time_utc,c.now+timedelta(minutes=1440))


class ProtectionEntryTests(unittest.TestCase):
    for _name in ('setUp','tearDown','provider','checkpoint','receipts','bars','setup_runtime','account','prepare_response','reports','fill_events'):
        locals()[_name]=getattr(mf.FactorRuntimeTests,_name)
    del _name

    def test_real_fixed_signal_skips_locked_pair_without_consuming_a_post(self):
        c=self.setup_runtime();p=NativeProtections(self.root/'entry-protections',c.gate,c.wallet,c.strategy,create=True);self.opened.append(p)
        with c.wallet.clock.at(c.now):
            PairLocks.lock_pair(c.universe[0],c.now+timedelta(hours=1),'synthetic existing lock',now=c.now,side='long')
            skipped=c.runtime.entry_step(c.strategy,c.inputs,c.liquidity,c.ex,c.now)
            self.assertEqual(skipped['result']['state'],'SKIPPED')
            self.assertEqual(skipped['result']['sizing']['reasons'],['native_protection_lock']);self.assertEqual(c.ex._api.requests,[])
            rid=self.prepare_response(c,1)
            submitted=c.runtime.entry_step(c.strategy,c.inputs,c.liquidity,c.ex,c.now)
            self.assertEqual(submitted['request_id'],rid);self.assertEqual(len(c.ex._api.requests),1)


if __name__=='__main__':unittest.main(verbosity=2)
