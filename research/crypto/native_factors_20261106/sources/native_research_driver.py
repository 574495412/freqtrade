"""Single registered data source for explicit synthetic observation/dispatch.

There is no polling, price invention or source receipt construction. Callers
must supply full source account events and local response transports separately.
"""
import copy
from contextlib import contextmanager
from contextvars import ContextVar

from native_factor_entries import EntryInputs
from native_factor_loop import NativeFactorLoop
from native_position_targets import PositionReductionInputs
from native_research_startup import NativeResearchStartup
from native_strategy_exits import ExitBarInputs, _frame_evidence
from rd1 import RiskError, StateError, digest, stamp

_DRIVER = ContextVar('native_research_driver', default=None)


def require_driver(gate, now, phase):
    binding = gate.s.get('required_research_driver')
    if binding is None:
        return
    active = _DRIVER.get()
    driver = getattr(gate, 'research_driver', None)
    if (driver is None or driver.binding != binding or active != (driver, now, phase)):
        raise StateError('Registered research driver must own this observation/dispatch')
    driver._attached(now)


class NativeResearchDriver:
    def __init__(self, startup, loop):
        if (type(startup) is not NativeResearchStartup or type(loop) is not NativeFactorLoop
                or startup.gate is not loop.gate or startup.wallet is not loop.wallet
                or not startup.started):
            raise RiskError('Research driver needs the started account and its native loop')
        self.startup, self.loop = startup, loop
        self.gate, self.wallet = loop.gate, loop.wallet
        self.closed = False
        self.binding = dict(policy='registered_native_data_explicit_events_v1',
            startup=copy.deepcopy(startup.binding), loop=copy.deepcopy(self.gate.s['factor_loop_binding']))
        old = self.gate.s.get('required_research_driver')
        if old not in (None, self.binding):
            raise StateError('Registered research driver binding changed')
        self.gate.s['required_research_driver'] = copy.deepcopy(self.binding)
        self.gate._persist()
        self.gate.research_driver = self

    def _attached(self, now, bot=None):
        self.wallet.clock.require(now)
        s = self.startup
        if (self.closed or self.gate._poisoned or self.gate.store.lock.closed
                or s._poisoned or s.store.lock.closed or not s.started or s.s['state'] != 'COMPLETE'
                or self.gate.s.get('required_research_driver') != self.binding
                or getattr(self.gate, 'research_driver', None) is not self
                or getattr(self.gate, 'research_startup', None) is not s
                or self.gate.s['factor_loop_binding'] != self.binding['loop']):
            raise StateError('Research driver runtime binding unavailable')
        s._validate_config()
        if bot is not None and (bot.strategy is not s.strategy or bot.wallets is not self.wallet.wallets):
            raise RiskError('Research driver bot differs from its started account')

    @contextmanager
    def _scope(self, now, phase):
        if _DRIVER.get() is not None:
            raise StateError('Nested research driver calls forbidden')
        token = _DRIVER.set((self, now, phase))
        try:
            yield
        finally:
            _DRIVER.reset(token)

    def observe(self, batch_id, evidence, receipt, quotes, reports, events, bot, now, *,
                complete_sequence, complete_from_inception, funding_complete):
        # Observation is allowed to repair a pending wallet/account batch. Do
        # not demand entry-clear permission before processing authoritative data.
        self._attached(now, bot)
        with self._scope(now, 'observe'):
            result = self.loop.observe(batch_id, evidence, receipt, quotes, reports, events, bot, now,
                complete_sequence=complete_sequence, complete_from_inception=complete_from_inception,
                funding_complete=funding_complete)
        self.startup.ready(now)
        return result

    def _frames(self, now):
        dp = self.startup.dp
        dp.at(now)
        hours, main, errors = {}, {}, []
        for pair in self.loop.runtime.universe:
            for timeframe in ('1h', self.startup.strategy.timeframe):
                try:
                    frame = dp.get_pair_dataframe(pair, timeframe)
                except (RiskError, StateError) as exc:
                    frame = None
                    errors.append(dict(pair=pair, timeframe=timeframe, error_type=type(exc).__name__, reason=str(exc)))
                if timeframe == '1h':
                    if frame is not None:
                        hours[pair] = frame
                else:
                    main[pair] = frame
        source = dp.source_id
        entries = {p: EntryInputs(main[p], hours, source) for p in self.loop.runtime.universe}
        exits = {p: ExitBarInputs(main[p], hours, source) for p in self.loop.runtime.universe}
        reductions = None if errors else PositionReductionInputs(hours, source)
        proof = dict(source_id=source, data_hash=self.startup.binding['data_hash'], errors=errors,
            primary={p: None if f is None else _frame_evidence(f) for p, f in main.items()},
            hourly={p: _frame_evidence(f) for p, f in hours.items()})
        return entries, exits, reductions, proof

    def dispatch(self, bot, now, *, cancel_exchanges, exit_exchanges, exit_rates,
                 entry_exchange=None, entry_liquidity=None, allow_entry=True):
        self._attached(now, bot)
        checked = self.wallet._checked_evidence
        if checked is None:
            raise RiskError('Research dispatch needs a complete account checkpoint')
        key = str(checked.snapshot.cursor)
        cycle = self.gate.s.get('dispatch_cycles', {}).get(key)
        with self._scope(now, 'dispatch'):
            args = dict(cancel_exchanges=cancel_exchanges, exit_exchanges=exit_exchanges,
                exit_rates=exit_rates, entry_exchange=entry_exchange, entry_liquidity=entry_liquidity,
                allow_entry=allow_entry)
            # Preserve the existing parent fence after a POST/DELETE; no fresh
            # wallet or signal read is needed merely to report WAIT_ACCOUNT.
            if cycle and cycle['state'] in ('DISPATCH_STARTED', 'AWAIT_ACCOUNT'):
                return self.loop.dispatch(bot, now, strategy_inputs={}, reduction_inputs=None, **args)
            self.startup.ready(now)
            decision = self.loop.risk.evaluate(checked.snapshot, now)
            urgent = any(a['kind'] in ('CANCEL_ENTRY', 'EXIT_POSITION') for a in decision.actions)
            if urgent:
                entries, exits, reductions = None, {}, None
                proof = dict(source_id=self.startup.dp.source_id, data_hash=self.startup.binding['data_hash'],
                    mode='risk_first_without_signal_read', errors=[])
            else:
                entries, exits, reductions, proof = self._frames(now)
                # Exit evaluation concerns current positions. The durable data
                # receipt still covers the entire registered universe once.
                held = {p.pair for p in checked.snapshot.positions}
                exits = {p: value for p, value in exits.items() if p in held}
            record = dict(at=stamp(now), cursor=checked.snapshot.cursor, binding=self.binding, data=proof)
            frames = self.gate.s.setdefault('research_driver_frames', {})
            if key in frames and digest(frames[key]) != digest(record):
                raise StateError('Research driver data changed within the same account cursor')
            if key not in frames:
                frames[key] = record
                self.gate._persist()
            if proof['errors']:
                # Corrupt/missing data cannot authorize new risk or allocation.
                # Native stop/ROI can still use independent price evidence.
                args['allow_entry'] = False
            result = self.loop.dispatch(bot, now, strategy_inputs=exits, reduction_inputs=reductions,
                entry_inputs=entries, **args)
            if proof['errors']:
                result = dict(result, data_errors=copy.deepcopy(proof['errors']))
                if result['state'] == 'IDLE':
                    result['state'] = 'DATA_BLOCKED'
            return result

    def close(self):
        self.closed = True
