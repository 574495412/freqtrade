"""Preserve base quantity while honoring native reserve and real market limits."""
import importlib.util
import sys
from pathlib import Path

import pytest

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT.parent/'CZ-Agent/vendor/freqtrade'))
spec=importlib.util.spec_from_file_location('reservation',ROOT/'freqtrade/data/carry_reservation.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
from freqtrade.exchange.exchange_utils import amount_to_contract_precision


def market():return {'contractSize':1.,'limits':{'amount':{'min':.001},'cost':{'min':50}},'precision':{'amount':.001}}


def test_native_reserve_is_funded_without_raising_actual_base_quantity():
    q=.001;rate=93648.3;minimum=98.330715
    stake=m.reserve_for_quantity(q,rate,minimum,123.75,market(),.0015)
    assert stake==minimum and stake*(1+.0015)<=123.75
    assert amount_to_contract_precision(stake/rate,.001,4,1)==q


def test_normal_existing_1000_leg_uses_exact_original_native_request():
    q=.002;rate=93648.3
    assert m.reserve_for_quantity(q,rate,98.330715,247.5,market(),.0015)==q*rate


@pytest.mark.parametrize('fault',['qty_changes','wallet','raw_cost','raw_amount','nan','contract','unknown_min'])
def test_reservation_cannot_rescue_a_quantity_or_budget_violation(fault):
    args=[.001,93648.3,98.330715,123.75,market(),.0015]
    if fault=='qty_changes':args[2]=200
    if fault=='wallet':args[3]=98.34
    if fault=='raw_cost':args[4]['limits']['cost']['min']=100
    if fault=='raw_amount':args[4]['limits']['amount']['min']=.002
    if fault=='nan':args[1]=float('nan')
    if fault=='contract':args[4]['contractSize']=10
    if fault=='unknown_min':args[2]=None
    with pytest.raises(ValueError):m.reserve_for_quantity(*args)
