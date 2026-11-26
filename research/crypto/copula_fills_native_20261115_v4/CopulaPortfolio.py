"""Native fixed-unit paired callbacks; real source accounts remain guarded."""
import hashlib
import importlib.util
from math import isclose
from pathlib import Path

import pandas as pd
from ExecutionInputs import ExecutionInputs

from freqtrade.persistence import Trade


class CopulaPortfolio(ExecutionInputs):
    position_adjustment_enable = False
    order_types = {'entry': 'market', 'exit': 'market', 'stoploss': 'market', 'stoploss_on_exchange': False}

    def ft_bot_start(self, **kwargs):
        # A separate fixture subclass admits only registered synthetic inputs.
        # Complete source20 financial admission remains unresolved.
        raise ValueError('Source20 Copula funded metadata/paired-account admission remains incomplete')

    def bot_start(self, **kwargs):
        super().bot_start(**kwargs)
        self._execution_setup()

    def _execution_setup(self):
        path = Path(self.config['research_root']) / 'freqtrade/data/copula_execution.py'
        if hashlib.sha256(path.read_bytes()).hexdigest() != self.config['copula_helper_sha256']:
            raise ValueError('Frozen execution helper changed')
        spec = importlib.util.spec_from_file_location('copula_fills_helper', path)
        self._owner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self._owner)
        self._prices = {}
        for pair in self.config['exchange']['pair_whitelist']:
            frame = self.dp.get_pair_dataframe(pair, '5m', 'futures')
            self._prices[pair] = frame.set_index('date').open
        self._events = {}
        for intent in self.research_intents:
            self._events.setdefault(intent['at'], []).append(intent)
        self._weekly, self._expected, self._context = {}, {}, None
        self._fatal_error, self._last_clock = None, None
        self.research_checked_clocks = 0
        self.research_trace = {key: [] for key in ['budgets', 'admissions', 'entry_confirmations', 'filled_orders', 'phase_checks', 'errors']}

    def _fail(self, stage, error, current_time):
        if self._fatal_error is None:
            self._fatal_error = {'stage': stage, 'error': str(error), 'at': str(current_time)}
            self.research_trace['errors'].append(self._fatal_error)

    def _actual(self):
        result = {}
        for trade in Trade.get_trades_proxy(is_open=True):
            if trade.pair in result or trade.has_open_orders or trade.amount <= 0:
                raise ValueError('Duplicate, pending or nonpositive native paired holding')
            result[trade.pair] = {'quantity': trade.amount, 'side': trade.trade_direction}
        return result

    def _check_actual(self):
        actual = self._actual()
        if set(actual) != set(self._expected):
            raise ValueError(f'Native paired holdings differ: actual={actual}, expected={self._expected}')
        for pair, expected in self._expected.items():
            if actual[pair]['side'] != expected['side'] or not isclose(actual[pair]['quantity'], expected['quantity'], rel_tol=0, abs_tol=1e-10):
                raise ValueError('Native direction or fixed weekly quantity changed')
        return actual

    def _rates(self, legs, stamp):
        return {symbol + '/USDT:USDT': float(self._prices[symbol + '/USDT:USDT'].loc[stamp]) for symbol, _ in legs}

    def _prepare_entry(self, intent, stamp):
        if self._expected:
            raise ValueError('Paired entry must follow an actual flat native boundary')
        frozen = self._weekly[intent['week']]
        rates = self._rates(intent['legs'], stamp)
        admission = self._owner.joint_entry_admission(self.wallets, self.dp._exchange, frozen, rates, self.config['fee'])
        self.research_trace['admissions'].append({'at': str(stamp), 'week': intent['week'], **admission})
        if admission['status'] != 'ADMIT_BOTH':
            self._context = {'at': stamp, 'action': 'rejected_pair', 'expected': {}}
            return
        targets = {}
        for index, (symbol, _) in enumerate(intent['legs']):
            pair = symbol + '/USDT:USDT'
            sign = int(intent['state']) * (-1 if index == 0 else 1)
            targets[pair] = {'quantity': frozen['quantities'][pair], 'side': 'long' if sign > 0 else 'short'}
        self._context = {'at': stamp, 'action': 'entry', 'targets': targets, 'rates': rates, 'admission': admission}
        self._expected = targets

    def bot_loop_start(self, current_time, **kwargs):
        if self._fatal_error:
            return
        stamp = pd.Timestamp(current_time)
        try:
            if self._last_clock is not None and stamp <= self._last_clock:
                raise ValueError('Native paired clock must increase')
            actual = self._check_actual()
            if self._context is not None:
                self.research_trace['phase_checks'].append({'at': str(stamp), 'completed_phase': self._context['action'], 'actual': actual})
            self._context, self._last_clock = None, stamp
            self.research_checked_clocks += 1
            for intent in self._events.get(stamp, []):
                if intent['action'] == 'budget':
                    if self._expected:
                        raise ValueError('Weekly budget did not reach actual flat state')
                    legs = tuple((s + '/USDT:USDT', beta) for s, beta in intent['legs'])
                    frozen = self._owner.freeze_weekly_units(self.wallets, self.dp._exchange, legs, self._rates(intent['legs'], stamp))
                    self._weekly[intent['week']] = frozen
                    self.research_trace['budgets'].append({'at': str(stamp), 'week': intent['week'], **frozen})
                    self._context = {'at': stamp, 'action': 'budget'}
                elif intent['action'] == 'exit':
                    self._context = {'at': stamp, 'action': 'exit', 'previous': self._expected.copy()}
                    self._expected = {}
                else:
                    self._prepare_entry(intent, stamp)
        except Exception as exc:
            self._fail('bot_loop_start', exc, current_time)

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs):
        if max_leverage < 3:
            self._fail('leverage', 'Native market cannot support frozen threefold margin', current_time)
        return 3.

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake, leverage, entry_tag, side, **kwargs):
        try:
            context = self._context
            if self._fatal_error or context is None or context['action'] != 'entry' or context['at'] != pd.Timestamp(current_time):
                return 0.
            expected = context['targets'][pair]
            stake = context['admission']['requested_stakes'][pair]
            if side != expected['side'] or leverage != 3 or not (min_stake or 0) <= stake <= max_stake:
                raise ValueError('Native callback cannot preserve registered paired stake')
            if not isclose(current_rate, context['rates'][pair], rel_tol=0, abs_tol=1e-10):
                raise ValueError('Native entry price differs from same-clock opening price')
            return stake
        except Exception as exc:
            self._fail('custom_stake_amount', exc, current_time)
            return 0.

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs):
        try:
            context = self._context
            if self._fatal_error or context is None or context['action'] != 'entry' or context['at'] != pd.Timestamp(current_time):
                return False
            target = context['targets'][pair]
            if order_type != 'market' or side != target['side'] or not isclose(amount, target['quantity'], rel_tol=0, abs_tol=1e-10):
                raise ValueError('Native amount/direction changed after paired preflight')
            if not isclose(rate, context['rates'][pair], rel_tol=0, abs_tol=1e-10):
                raise ValueError('Native confirmed price changed')
            if pair in self._actual():
                raise ValueError('Duplicate native entry into fixed-unit leg')
            self.research_trace['entry_confirmations'].append({'at': str(current_time), 'pair': pair, 'quantity': amount, 'rate': rate, 'side': side})
            return True
        except Exception as exc:
            self._fail('confirm_trade_entry', exc, current_time)
            return False

    def confirm_trade_exit(self, pair, trade, order_type, amount, rate, time_in_force, exit_reason, current_time, **kwargs):
        if exit_reason == 'force_exit':
            return True
        try:
            context = self._context
            if self._fatal_error:
                return False
            if exit_reason not in {'exit_signal', 'copula_neutral_or_week_boundary'} or context is None or context['action'] != 'exit' or context['at'] != pd.Timestamp(current_time):
                raise ValueError(f'Unplanned native risk/exit event requires separate policy admission: {exit_reason}')
            expected = context['previous'][pair]
            if not isclose(amount, expected['quantity'], rel_tol=0, abs_tol=1e-10):
                raise ValueError('Partial exit is not the frozen paired exit')
            return True
        except Exception as exc:
            self._fail('confirm_trade_exit', exc, current_time)
            return False

    def order_filled(self, pair, trade, order, current_time, **kwargs):
        self.research_trace['filled_orders'].append({'at': str(current_time), 'pair': pair, 'trade_id': trade.id,
            'order_id': order.order_id, 'side': order.ft_order_side, 'quantity': order.safe_filled,
            'rate': order.safe_price, 'is_short': trade.is_short})
