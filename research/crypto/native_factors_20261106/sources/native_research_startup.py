"""Explicit synthetic research startup through official Backtesting methods.

No Backtesting/Exchange/FreqtradeBot constructor, disk market-data fallback,
live startup, RPC transport or financial admission is exposed here. This is an
in-process initializer for the existing isolated replay, not a complete runner.
"""
import copy
from contextvars import ContextVar
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from freqtrade.data.dataprovider import DataProvider
from freqtrade.enums import CandleType, RunMode, TradingMode
from freqtrade.optimize.backtesting import Backtesting
from native_factor_entries import validate_fixed_strategy
from native_protections import NativeProtections
from native_evidence import decimal_text
from native_strategy_exits import _closed, _frame_evidence
from rd1 import RiskError, StateError, Store, dec, digest, stamp


_STARTUP = ContextVar('synthetic_native_research_startup', default=None)


def require_scoped_startup(strategy):
    scope = _STARTUP.get()
    if scope is None:
        return False
    runtime, now = scope
    if strategy is not runtime.strategy:
        raise StateError('Research startup capability belongs to another strategy')
    runtime._validate(now)
    runtime._callback_seen = True
    return True


def _config_hash(config):
    return digest(json.loads(json.dumps(config, sort_keys=True, default=str)))


class SyntheticResearchData(DataProvider):
    """Actual DataProvider cutoff logic, with explicit local OHLCV only.

historic_ohlcv has the official full-history meaning. get_pair_dataframe uses
the native timeframe-specific cutoff, and requires an explicit replay clock.
"""
    def __init__(self, config, frames, clock, universe, source_id):
        if not source_id or config.get('runmode') != RunMode.BACKTEST:
            raise RiskError('Explicit synthetic backtest data source required')
        expected = {(p, tf) for p in universe for tf in (config['timeframe'], '1h')}
        if set(frames) != expected:
            raise RiskError('Synthetic startup data must cover the full frozen universe/timeframes')
        super().__init__(copy.deepcopy(config), None)
        self.clock = clock
        self.source_id = source_id
        self.frames = {}
        self.now = None
        for key, frame in frames.items():
            if frame is None or frame.empty:
                raise RiskError('Synthetic startup frame is empty')
            minutes = 60 if key[1] == '1h' else (5 if key[1] == '5m' else 240)
            # Validate every supplied row, including the future part of a tape.
            end = pd.Timestamp(frame.date.max()) + pd.Timedelta(minutes=minutes)
            validated = _closed(frame, minutes, end.to_pydatetime())
            if len(validated) != len(frame):
                raise RiskError('Synthetic startup frame contains invalid dates')
            self.frames[key] = validated
        self.manifest = {p+'|'+tf: _frame_evidence(f) for (p, tf), f in self.frames.items()}

    def at(self, now):
        self.clock.require(now)
        self.now = now
        self._set_dataframe_max_date(now)

    def historic_ohlcv(self, pair, timeframe, candle_type=''):
        if self.now is None:
            raise StateError('Synthetic data needs an explicit replay time')
        self.clock.require(self.now)
        if self.runmode != RunMode.BACKTEST or self._exchange is not None:
            raise RiskError('Synthetic provider mode/transport changed')
        kind = CandleType.from_string(candle_type) if candle_type else self._default_candle_type
        key = (pair, timeframe)
        if kind != self._default_candle_type or key not in self.frames:
            raise RiskError('No explicit synthetic frame for requested pair/timeframe/type')
        frame = self.frames[key]
        if _frame_evidence(frame) != self.manifest[pair+'|'+timeframe]:
            raise StateError('Synthetic frame changed after registration')
        return frame.copy(deep=True)


class NativeResearchStartup:
    def __init__(self, directory, gate, wallet, strategy, frames, source_id, *, create=False):
        if gate.runtime_barrier is not wallet or gate.after_fill_guard is None:
            raise RiskError('Research startup requires the registered native account')
        self.gate, self.wallet, self.strategy = gate, wallet, strategy
        self.risk = gate.lifecycle.risk
        self.universe = tuple(self.risk.plan['markets'][self.risk.market]['pairs'])
        self._poisoned = False
        self.started = False
        self.protections = None
        self.config_hash = _config_hash(strategy.config)
        self.dp = SyntheticResearchData(strategy.config, frames, wallet.clock, self.universe, source_id)
        self.binding = dict(directory=str(Path(directory).resolve()), strategy=type(strategy).__name__,
            config_hash=self.config_hash, data_hash=digest(self.dp.manifest), source_id=source_id,
            evidence_class='synthetic', protection_hash=digest(self.risk.r['native_protections']))
        # Check before registering anything, including against a saved binding.
        self._validate_config()
        if gate.s.get('required_research_startup') not in (None, self.binding):
            raise StateError('Registered research startup binding changed')
        self.store = Store(Path(directory), dict(self.risk.s['identity'], layer='native_research_startup_v1'), create=create)
        try:
            if create:
                self.s = dict(schema_version=1, sequence=0, identity=self.store.identity,
                    binding=self.binding, state='REGISTERED', attempts=[])
                self._persist()
            else:
                self.s = self.store.load()
                if self.s['binding'] != self.binding:
                    raise StateError('Saved research startup inputs changed')
            gate.s['required_research_startup'] = copy.deepcopy(self.binding)
            gate._persist()
            gate.research_startup = self
        except Exception:
            self.store.close()
            raise

    def _validate_config(self):
        cfg = self.strategy.config
        expected = validate_fixed_strategy(self.strategy, self.universe)
        for name in ('bot_start', 'ft_bot_start', 'load_freqAI_model', 'ft_load_hyper_params'):
            if getattr(getattr(self.strategy, name), '__code__', None) != getattr(expected, name).__code__:
                raise RiskError('Research startup method differs from fixed source')
        if (cfg.get('runmode') != RunMode.BACKTEST or cfg.get('trading_mode') != self.risk.market
                or cfg.get('candle_type_def') != CandleType(self.risk.market)
                or cfg.get('timeframe') != self.strategy.timeframe
                or cfg.get('dry_run') is not True or cfg.get('enable_protections') is not True
                or cfg.get('stake_currency') != 'USDT' or cfg.get('dry_run_wallet') != 1000
                or cfg.get('stake_amount') != 'unlimited' or dec(decimal_text(cfg.get('fee'))) != self.risk.cost
                or cfg.get('max_open_trades') != 6 or cfg.get('tradable_balance_ratio') != .99
                or not self.strategy.position_adjustment_enable or cfg.get('position_adjustment_enable') is not True
                or self.strategy.startup_candle_count != (2028 if type(self.strategy).__name__ == 'F1Momentum168Native' else 0)
                or not self.strategy.use_exit_signal or self.strategy.exit_profit_only
                or self.strategy.ignore_roi_if_entry_signal or self.strategy.exit_profit_offset != 0
                or self.strategy.order_types.get('exit') != 'limit'
                or self.strategy.order_time_in_force.get('exit') != 'GTC'
                or self.strategy.ft_used_params
                or cfg.get('freqai', {}).get('enabled')
                or cfg.get('external_message_consumer', {}).get('producers')
                or any(cfg.get(k, {}).get('enabled') for k in ('telegram', 'api_server', 'webhook'))
                or any(cfg.get('exchange', {}).get(k) for k in ('key', 'secret', 'password', 'uid'))
                or self.wallet.provider.evidence_class != 'synthetic'
                or _config_hash(cfg) != self.config_hash):
            raise RiskError('Research startup requires fixed synthetic backtest configuration')
        if self.risk.market == 'futures' and cfg.get('margin_mode') != 'isolated':
            raise RiskError('Research futures startup requires isolated margin')

    def _validate(self, now):
        self.wallet.clock.require(now)
        self._validate_config()
        self.gate.require_account_batch_clear()
        self.wallet.require_entry_clear(now)
        if self._poisoned or self.store.lock.closed:
            raise StateError('Research startup journal unavailable')
        if (self.gate.s.get('required_research_startup') != self.binding
                or getattr(self.gate, 'research_startup', None) is not self):
            raise StateError('Research startup no longer owns the account binding')

    def _persist(self):
        if self._poisoned or self.store.lock.closed:
            raise StateError('Research startup journal unavailable')
        self.s['sequence'] += 1
        try:
            self.store.save(self.s)
        except Exception as exc:
            self._poisoned = True
            raise StateError('Research startup acknowledgement lost; reopen and verify') from exc

    def run(self, now):
        self._validate(now)
        if self.started:
            self.ready(now)
            return copy.deepcopy(self.s['attempts'][-1])
        if self.s['state'] == 'PREPARED':
            raise StateError('Interrupted research startup remains unverified')
        if _STARTUP.get() is not None:
            raise StateError('Nested research startup forbidden')
        # Normal entry callbacks must be disabled at startup. During later
        # native submission the existing exact-request capability deliberately
        # replaces them; the durable submit gate validates that request itself.
        expected = validate_fixed_strategy(self.strategy, self.universe)
        for name in ('confirm_trade_entry', 'custom_stake_amount'):
            if getattr(getattr(self.strategy, name), '__code__', None) != getattr(expected, name).__code__:
                raise RiskError('Research startup entry callback differs from fixed source')
        self.s['state'] = 'PREPARED'
        self.s['attempts'].append(dict(at=stamp(now), account_cursor=self.wallet.provider.s['last_cursor'],
            state='PREPARED', financial_admission=False))
        self._persist()
        self.dp.at(now)
        self.strategy.protections = copy.deepcopy(self.risk.r['native_protections'])
        proxy = SimpleNamespace(config=self.strategy.config, dataprovider=self.dp,
            wallets=self.wallet.wallets, trading_mode=TradingMode(self.risk.market))
        self._callback_seen = False
        token = _STARTUP.set((self, now))
        try:
            Backtesting._set_strategy(proxy, self.strategy)
            if not self._callback_seen:
                raise StateError('Native bot_start callback was not observed')
            self._validate(now)  # hyperparameter loading must not change frozen values
            Backtesting._load_protections(proxy, self.strategy)
            protection_dir = self.store.directory / 'protections'
            self.protections = NativeProtections(protection_dir, self.gate, self.wallet, self.strategy,
                create=not protection_dir.exists(), manager=proxy.protections)
            self.protections.ready(now)
            self.s['state'] = 'COMPLETE'
            self.s['attempts'][-1].update(state='COMPLETE', native_callback_seen=True,
                protection_binding=self.protections.binding, native_can_short=proxy._can_short)
            self._persist()
            self.started = True
        finally:
            _STARTUP.reset(token)
        return copy.deepcopy(self.s['attempts'][-1])

    def ready(self, now):
        self._validate(now)
        if not self.started or self.s['state'] != 'COMPLETE':
            raise StateError('Research startup has not completed in this process')
        if (self.strategy.dp is not self.dp or self.strategy.wallets is not self.wallet.wallets
                or self.protections is None or getattr(self.gate, 'native_protections', None) is not self.protections
                or self.strategy.protections != self.risk.r['native_protections']
                or self.dp.clock is not self.wallet.clock or self.dp._exchange is not None
                or _config_hash(self.dp._config) != self.config_hash
                or digest(self.dp.manifest) != self.binding['data_hash']):
            raise StateError('Research startup runtime attachment changed')
        self.protections.ready(now)
        self.dp.at(now)

    def close(self):
        self.started = False
        if self.protections is not None:
            self.protections.close()
        self.store.close()
