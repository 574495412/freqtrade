"""Exact native-phase forwarding and failure boundaries, not economic tests."""
import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT.parent / 'CZ-Agent/vendor/freqtrade'))
spec = importlib.util.spec_from_file_location('tested_copula_admission', ROOT / 'freqtrade/optimize/copula_admission.py')
guard = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = guard
spec.loader.exec_module(guard)
from freqtrade.enums import RunMode, TradingMode
from freqtrade.persistence import Trade

PAIRS = ('FAKEONE/USDT:USDT', 'FAKETWO/USDT:USDT')
AT = datetime(2025, 1, 2, 1, tzinfo=UTC)


class NativePhase:
    def _time_pair_generator_det(self, at, pairs):
        for pair in pairs:
            self.forwarded.append(pair)
            yield (at, True, False, 0, pair)


class Probe(guard.CopulaRunAdmissionMixin, NativePhase):
    pass


@pytest.fixture(autouse=True)
def isolated():
    with patch.object(Trade, 'use_db', False):
        yield


def model(gaps=()):
    a = Probe()
    a.config = {'runmode': RunMode.BACKTEST, 'dry_run': True,
                'exchange': {'pair_whitelist': list(PAIRS)}}
    a.trading_mode, a.timeframe = TradingMode.FUTURES, '5m'
    a.timeframe_detail_td = 0
    a.dynamic_pairlist = a._position_stacking = False
    a.forwarded = []
    a.strategy = SimpleNamespace(_fatal_error=None, _actual=lambda: {}, _check_actual=lambda: {})
    a.setup_copula_run_admission(pairs=PAIRS, gaps=gaps)
    return a


def test_all_parent_events_forward_unchanged_in_original_order():
    a = model()
    assert list(a._time_pair_generator_det(AT, list(PAIRS))) == [(AT, True, False, 0, p) for p in PAIRS]
    assert a.forwarded == list(PAIRS) and a.copula_phase_checks == 1


def test_known_missing_event_held_aborts_before_any_native_event():
    a = model((guard.MissingFundingEvent(PAIRS[0], AT, 'a' * 64),))
    a.strategy._actual = lambda: {PAIRS[0]: {'quantity': 4.2, 'side': 'short'}}
    with pytest.raises(guard.CopulaAccountAdmissionError, match='missing_funding_while_held'):
        list(a._time_pair_generator_det(AT, list(PAIRS)))
    assert not a.forwarded and a.copula_phase_checks == 0
    assert a.copula_gap_checks[0]['rate'] is None and a.copula_gap_checks[0]['exposed']


def test_missing_event_flat_remains_unknown_and_does_not_skip_events():
    a = model((guard.MissingFundingEvent(PAIRS[0], AT, 'a' * 64),))
    assert len(list(a._time_pair_generator_det(AT, list(PAIRS)))) == 2
    assert a.copula_gap_checks[0]['rate'] is None
    assert a.copula_gap_checks[0]['quantity_before_event'] == 0


def test_no_prefetch_and_pair_failure_is_at_completed_same_clock():
    a = model()
    def invalid():
        raise ValueError('Actual one leg only')
    a.strategy._check_actual = invalid
    iterator = a._time_pair_generator_det(AT, list(PAIRS))
    first = next(iterator)
    assert first[-1] == PAIRS[0] and a.forwarded == [PAIRS[0]]
    assert next(iterator)[-1] == PAIRS[1]
    with pytest.raises(guard.CopulaAccountAdmissionError, match='paired_phase'):
        next(iterator)
    assert a.copula_admission_failure['at'] == str(AT)


def test_closing_partially_consumed_iterator_does_not_claim_completed_phase():
    a = model()
    iterator = a._time_pair_generator_det(AT, list(PAIRS))
    next(iterator)
    iterator.close()
    assert a.copula_phase_checks == 0 and a.copula_admission_failure is None


def test_prior_callback_fatal_cannot_be_swallowed_by_native_wrapper():
    a = model()
    a.strategy._fatal_error = {'stage': 'custom_stake_amount', 'error': 'bad'}
    with pytest.raises(guard.CopulaAccountAdmissionError, match='strategy_callback'):
        list(a._time_pair_generator_det(AT, list(PAIRS)))
    assert not a.forwarded


@pytest.mark.parametrize('fault', ['mode', 'dry', 'spot', 'timeframe', 'detail', 'dynamic', 'stack', 'db', 'universe'])
def test_unsupported_account_contract_rejects(fault):
    a = model()
    if fault == 'mode': a.config['runmode'] = RunMode.DRY_RUN
    elif fault == 'dry': a.config['dry_run'] = False
    elif fault == 'spot': a.trading_mode = TradingMode.SPOT
    elif fault == 'timeframe': a.timeframe = '1h'
    elif fault == 'detail': a.timeframe_detail_td = 1
    elif fault == 'dynamic': a.dynamic_pairlist = True
    elif fault == 'stack': a._position_stacking = True
    elif fault == 'db': Trade.use_db = True
    elif fault == 'universe': a.config['exchange']['pair_whitelist'] = list(PAIRS[:1])
    with pytest.raises(ValueError): a.setup_copula_run_admission(pairs=PAIRS, gaps=())


def test_changed_universe_rejects_before_next_phase():
    a = model()
    a.config['exchange']['pair_whitelist'].reverse()
    with pytest.raises(guard.CopulaAccountAdmissionError, match='universe changed'):
        list(a._time_pair_generator_det(AT, list(PAIRS)))
    assert not a.forwarded


@pytest.mark.parametrize('fault', ['mode', 'dry', 'db', 'detail'])
def test_account_context_is_rechecked_at_native_phase(fault):
    a = model()
    if fault == 'mode': a.config['runmode'] = RunMode.DRY_RUN
    elif fault == 'dry': a.config['dry_run'] = False
    elif fault == 'db': Trade.use_db = True
    elif fault == 'detail': a.timeframe_detail_td = 1
    with pytest.raises(guard.CopulaAccountAdmissionError, match='contract changed'):
        list(a._time_pair_generator_det(AT, list(PAIRS)))
    assert not a.forwarded


def test_unconfigured_boundary_never_silently_passes():
    a = model()
    del a._copula_admission_pairs
    with pytest.raises(guard.CopulaAccountAdmissionError, match='setup'):
        list(a._time_pair_generator_det(AT, list(PAIRS)))


@pytest.mark.parametrize('fault', ['naive', 'minute', 'source', 'pair'])
def test_funding_gap_needs_exact_clock_identity_and_source(fault):
    at, pair, source = AT, PAIRS[0], 'a' * 64
    if fault == 'naive': at = AT.replace(tzinfo=None)
    elif fault == 'minute': at = AT.replace(minute=5)
    elif fault == 'source': source = ''
    elif fault == 'pair': pair = 'ABC/USD'
    with pytest.raises(ValueError): guard.MissingFundingEvent(pair, at, source)
