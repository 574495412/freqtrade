"""Synthetic native startup, restart barriers and native DataProvider cutoffs."""
from datetime import timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'sources'))
import test_native_factor_runtime as mf
from test_native_factors import resolved
from native_research_startup import NativeResearchStartup
from native_entries import NativeEntryGate
from freqtrade.data.dataprovider import DataProvider
from freqtrade.enums import CandleType, RunMode
from freqtrade.exceptions import OperationalException
from freqtrade.optimize.backtesting import Backtesting
from rd1 import RiskError, StateError


class ResearchStartupTests(unittest.TestCase):
    for _name in ('setUp', 'tearDown', 'provider', 'checkpoint', 'receipts', 'bars', 'setup_runtime',
                  'account', 'prepare_response', 'observe', 'reports', 'fill_events'):
        locals()[_name] = getattr(mf.FactorRuntimeTests, _name)
    del _name

    def fixture(self, market='spot', name='F1Momentum168Native'):
        c = self.setup_runtime(market)
        c.strategy = resolved(name, c.universe)
        c.bot.strategy = c.strategy
        c.strategy.config.update(trading_mode=market, candle_type_def=CandleType(market),
            margin_mode='isolated' if market == 'futures' else '', enable_protections=True,
            position_adjustment_enable=True, fee=float(c.life.risk.cost))
        c.strategy.position_adjustment_enable = True
        day = pd.Timestamp(c.now).normalize()
        end = day + (pd.Timedelta(minutes=10) if c.strategy.timeframe == '5m' else pd.Timedelta(hours=4))
        main = self.bars(pd.date_range(end=end, periods=30,
            freq='5min' if c.strategy.timeframe == '5m' else '4h'), [10.] * 30)
        c.frames = {(pair, tf): (c.inputs[pair].hourly[pair] if tf == '1h' else main)
            for pair in c.universe for tf in (c.strategy.timeframe, '1h')}
        return c

    def make(self, c, create=True):
        s = NativeResearchStartup(self.root/'startup', c.gate, c.wallet, c.strategy,
            c.frames, 'synthetic-startup-bars', create=create)
        self.opened.append(s)
        return s

    def started(self, market='spot', name='F1Momentum168Native'):
        c = self.fixture(market, name)
        s = self.make(c)
        with c.wallet.clock.at(c.now):
            record = s.run(c.now)
            c.gate.require_research_startup(c.now)
            self.assertIs(s.protections.manager._config, c.strategy.config)
            self.assertIs(c.strategy.wallets, c.wallet.wallets)
            self.assertIsInstance(c.strategy.dp, DataProvider)
            self.assertTrue(record['native_callback_seen'])
            self.assertFalse(record['financial_admission'])
            self.assertFalse(record['native_can_short'])
            self.assertEqual(s.protections.manager.name_list, ['CooldownPeriod', 'StoplossGuard', 'MaxDrawdown'])
            self.assertEqual(s.run(c.now), record)
            self.assertEqual(len(s.s['attempts']), 1)
        # Successful scoped startup never enables normal bot_start or stake/entry callbacks.
        with self.assertRaises(OperationalException):
            c.strategy.bot_start()
        self.assertFalse(c.strategy.confirm_trade_entry())
        self.assertEqual(c.strategy.custom_stake_amount(), 0)
        return c, s

    def test_f1_spot_actual_official_startup(self): self.started()

    def test_f2_futures_actual_official_startup_keeps_fixed_ema_parameters(self):
        c, s = self.started('futures', 'F2Volatility24Native')
        self.assertEqual((c.strategy.buy_range_short.value, c.strategy.buy_range_long.value), (8, 21))

    def test_same_rd1_control_actual_official_startup(self):
        self.started('spot', 'AverageRD1ControlNative')

    def test_native_data_cutoff_copies_and_missing_sources_without_disk_fallback(self):
        c, s = self.started()
        pair = c.universe[0]
        with c.wallet.clock.at(c.now):
            with patch('freqtrade.data.dataprovider.load_pair_history', side_effect=AssertionError('disk fallback')):
                frame = s.dp.get_pair_dataframe(pair, '1h')
                self.assertEqual(len(frame), 169)
                self.assertLess(frame.date.max(), pd.Timestamp(c.now).floor('h'))
                frame.loc[0, 'close'] = 999999
                self.assertNotEqual(s.dp.get_pair_dataframe(pair, '1h').close.iloc[0], 999999)
                primary = s.dp.get_pair_dataframe(pair, '5m')
                self.assertFalse(primary.empty)
                self.assertEqual(primary.date.max(), pd.Timestamp(c.now)-pd.Timedelta(minutes=5))
                with self.assertRaisesRegex(RiskError, 'No explicit synthetic frame'):
                    s.dp.get_pair_dataframe(pair, '15m')
        with self.assertRaises(StateError): s.dp.get_pair_dataframe(pair, '1h')

    def test_data_tampering_and_malformed_future_row_rejected(self):
        c = self.fixture()
        key = (c.universe[0], c.strategy.timeframe)
        bad = c.frames[key].copy()
        bad.loc[bad.index[-1], 'high'] = -1
        c.frames[key] = bad
        with self.assertRaisesRegex(RiskError, 'OHLCV values'): self.make(c)
        c.frames[key] = bad.assign(high=11)
        s = self.make(c)
        with c.wallet.clock.at(c.now):
            s.run(c.now)
            s.dp.frames[key].loc[0, 'close'] *= 2
            with self.assertRaisesRegex(StateError, 'frame changed'):
                s.dp.get_pair_dataframe(*key)

    def test_live_hyperopt_freqai_wrong_market_and_missing_universe_rejected_before_registration(self):
        c = self.fixture()
        for mode in (RunMode.LIVE, RunMode.DRY_RUN, RunMode.HYPEROPT):
            with self.subTest(mode=mode), patch.dict(c.strategy.config, runmode=mode):
                with self.assertRaises(RiskError): self.make(c)
        for changed in ({'freqai': {'enabled': True}}, {'trading_mode': 'futures'}, {'dry_run_wallet': 1001}, {'fee': 0}):
            with self.subTest(changed=changed), patch.dict(c.strategy.config, changed):
                with self.assertRaises(RiskError): self.make(c)
        for key, value in (('startup_candle_count', 1), ('use_exit_signal', False), ('exit_profit_only', True)):
            with self.subTest(parameter=key), patch.object(c.strategy, key, value):
                with self.assertRaises(RiskError): self.make(c)
        key = next(iter(c.frames)); del c.frames[key]
        with self.assertRaisesRegex(RiskError, 'full frozen universe'): self.make(c)
        self.assertNotIn('required_research_startup', c.gate.s)

    def test_registered_but_not_started_blocks_native_entry_and_reopen_cannot_forget(self):
        c = self.fixture(); s = self.make(c)
        with c.wallet.clock.at(c.now):
            with self.assertRaisesRegex(StateError, 'has not completed'):
                c.gate.submit(None, 'unstarted', None, None, c.now)
        c.gate.close()
        gate = NativeEntryGate(c.gate.store.directory, c.life, after_fill_guard=c.guard)
        self.opened.append(gate)
        with self.assertRaisesRegex(StateError, 'startup must be reattached'):
            gate.submit(None, 'forgotten', None, None, c.now)

    def test_restart_reinitializes_only_with_current_account_and_same_data(self):
        c, s = self.started(); binding = s.protections.binding; s.close()
        s = self.make(c, create=False)
        with c.wallet.clock.at(c.now):
            with self.assertRaisesRegex(StateError, 'has not completed'): s.ready(c.now)
            s.run(c.now)
            self.assertEqual(s.protections.binding, binding)
            self.assertEqual(len(s.s['attempts']), 2)
            with patch.object(c.wallet, '_checked_evidence', None):
                with self.assertRaisesRegex(RiskError, 'fresh complete account'): s.ready(c.now)
        s.close()
        c.frames[next(iter(c.frames))] = c.frames[next(iter(c.frames))].iloc[:-1]
        with self.assertRaisesRegex(StateError, 'binding changed'): self.make(c, create=False)

    def test_interrupted_startup_stays_blocked_and_capability_is_revoked(self):
        c = self.fixture(); s = self.make(c)
        with c.wallet.clock.at(c.now):
            with patch.object(Backtesting, '_load_protections', side_effect=OSError('synthetic startup interruption')):
                with self.assertRaises(OSError): s.run(c.now)
            with self.assertRaises(OperationalException): c.strategy.bot_start()
            with self.assertRaisesRegex(StateError, 'has not completed'): s.ready(c.now)
        s.close(); s = self.make(c, create=False)
        with c.wallet.clock.at(c.now):
            with self.assertRaisesRegex(StateError, 'Interrupted research startup'): s.run(c.now)

    def test_completed_startup_ack_loss_can_reopen_without_losing_protection_identity(self):
        c = self.fixture(); s = self.make(c); save = s.store.save
        def lost(state):
            save(state)
            if state['state'] == 'COMPLETE': raise OSError('synthetic startup ack loss')
        with c.wallet.clock.at(c.now):
            with patch.object(s.store, 'save', side_effect=lost):
                with self.assertRaises(StateError): s.run(c.now)
            with self.assertRaises(StateError): c.gate.require_research_startup(c.now)
        binding = s.protections.binding; s.close(); s = self.make(c, create=False)
        with c.wallet.clock.at(c.now):
            s.run(c.now)
            self.assertEqual(s.protections.binding, binding)

    def test_config_method_and_runtime_replacement_rejected(self):
        c, s = self.started()
        with c.wallet.clock.at(c.now):
            with patch.dict(c.strategy.config, max_open_trades=7):
                with self.assertRaises(RiskError): s.ready(c.now)
            with patch.object(c.strategy, 'dp', None):
                with self.assertRaisesRegex(StateError, 'attachment changed'): s.ready(c.now)
            with patch.object(c.strategy, 'ft_bot_start', lambda: None):
                with self.assertRaisesRegex(RiskError, 'method differs'): s.ready(c.now)

    def test_started_account_reaches_actual_native_entry_through_scoped_callbacks(self):
        c, s = self.started()
        with c.wallet.clock.at(c.now):
            rid = self.prepare_response(c, 0)
            result = c.runtime.entry_step(c.strategy, c.inputs, c.liquidity, c.ex, c.now)
            self.assertEqual(result['request_id'], rid)
            self.assertIsNotNone(c.gate.s['requests'][rid]['native_binding'])
            self.assertEqual(len(c.ex._api.requests), 1)
        now = c.now + timedelta(seconds=1)
        reports = self.reports(c, now, fill=100)
        events = self.fill_events(c, reports, now)
        with c.wallet.clock.at(now):
            result = self.observe(c, 'startup-filled', reports, now, 2, events)
            self.assertEqual(len(result['evidence'].snapshot.positions), 1)
            c.guard.require_entry_clear()
            s.ready(now)
        self.assertFalse(c.strategy.confirm_trade_entry())
        self.assertEqual(c.strategy.custom_stake_amount(), 0)


if __name__ == '__main__':
    unittest.main()
