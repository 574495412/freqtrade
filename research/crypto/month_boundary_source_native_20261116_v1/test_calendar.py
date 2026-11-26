"""Leap/calendar boundary and source parity,with source financial engines unused."""
import ast
import datetime as dt
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

HERE = Path(__file__).absolute().parent
PATH = HERE.parents[2] / 'freqtrade/data/month_boundary.py'
spec = importlib.util.spec_from_file_location('local_month_signal', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def source_functions(path, names, namespace):
    tree = ast.parse(path.read_text())
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in selected} == set(names)
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), str(path), 'exec'), namespace)
    return namespace


def test_tdzn_source_calendar_exact_all_days_and_century_leap():
    source = source_functions(HERE / 'sources/TDZNBEER/run_turnofmonth.py',
                              ['is_tom', 'last_dom'], dict(dt=dt, HEAD=2, TAIL=3))
    times = pd.date_range('2000-01-01', '2101-12-31', tz='UTC')
    expected = [source['is_tom'](t.date(), source['last_dom'](t.date())) for t in times]
    assert module.month_boundary_state(times, last=3, first=2).tolist() == expected


def test_f1_source_anticipates_next_calendar_day_exact():
    source = source_functions(HERE / 'sources/f1devbin/src/crl/engine/rules.py',
                              ['turn_of_month'], dict(pd=pd))
    days = pd.date_range('2024-12-31', '2026-08-30', tz='UTC')
    flags = source['turn_of_month'](pd.Series(1., index=days), last=2, first=3)
    actual = module.month_boundary_state(days + pd.Timedelta(days=1), last=2, first=3)
    assert flags.tolist() == actual.tolist()


def test_only_calendar_matters_and_prefix_is_exact():
    dates = pd.date_range('2024-02-27', '2024-03-04', freq='h', tz='UTC')
    full = module.month_boundary_state(dates, last=2, first=3)
    assert not full.loc['2024-02-27'].any()
    assert full.loc['2024-02-28':'2024-03-03'].all()
    assert not full.loc['2024-03-04'].any()
    pd.testing.assert_series_equal(full.iloc[:25], module.month_boundary_state(dates[:25], last=2, first=3))


@pytest.mark.parametrize('fault', ['naive', 'nonutc', 'duplicate', 'reversed', 'nat', 'bool_count'])
def test_reject_ambiguous_clocks_and_parameters(fault):
    dates = pd.date_range('2025-01-01', periods=4, tz='UTC')
    last = 3
    if fault == 'naive': dates = dates.tz_localize(None)
    if fault == 'nonutc': dates = dates.tz_convert('Asia/Shanghai')
    if fault == 'duplicate': dates = dates.append(dates[-1:])
    if fault == 'reversed': dates = dates[::-1]
    if fault == 'nat': dates = dates.append(pd.DatetimeIndex([pd.NaT]))
    if fault == 'bool_count': last = True
    with pytest.raises(ValueError):
        module.month_boundary_state(dates, last=last, first=2)
