"""Explicitly test the captured-evidence fence of the existing synthetic RD1 route."""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

NATIVE = Path(__file__).resolve().parent.parent / 'native_factors_20261106'
sys.path.insert(0, str(NATIVE))

import test_native_factor_entries as entry_tests
import test_native_research_startup as startup_tests
from native_factor_entries import size_entry
from rd1 import RiskError


def test_captured_account_cannot_register_native_synthetic_startup():
    fixture = startup_tests.ResearchStartupTests()
    fixture.setUp()
    try:
        context = fixture.fixture()
        with patch.object(context.wallet.provider, 'evidence_class', 'captured'):
            with pytest.raises(RiskError, match='fixed synthetic backtest configuration'):
                fixture.make(context)
        assert 'required_research_startup' not in context.gate.s
        assert not (fixture.root / 'startup').exists()
    finally:
        fixture.tearDown()


def test_captured_liquidity_cannot_be_sized_against_synthetic_checkpoint():
    fixture = entry_tests.FixedEntryTests()
    fixture.setUp()
    try:
        context = fixture.prepare()
        _, exchange, _, _, provider, wallet, _, liquidity, now = context
        with wallet.clock.at(now):
            checked = fixture.ready(context)
            assert size_entry(provider, checked, exchange, wallet.wallets,
                              liquidity, Decimal(1), now)[0] is not None
            captured = replace(liquidity, evidence_class='captured', source_id='public-capture')
            with pytest.raises(RiskError, match='Entry liquidity/rules/scale/cost evidence invalid'):
                size_entry(provider, checked, exchange, wallet.wallets, captured, Decimal(1), now)
    finally:
        fixture.tearDown()
