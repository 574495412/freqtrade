"""Actual quote-prefix perturbation/truncation, independent of any account run."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

from MacroInputs import MacroClock, macro_headwind_at, parse_yahoo_macro

HERE=Path(__file__).absolute().parent


@pytest.mark.parametrize('clock',['2025-02-14T06:00:00+00:00','2025-11-04T06:00:00+00:00','2026-07-04T06:00:00+00:00'])
def test_actual_quote_future_perturbation_and_truncation(clock):
    at=datetime.fromisoformat(clock)
    receipts=json.loads((HERE.with_name('macro_transmission_source_native_20261116_v1')/'macro_http_receipts.json').read_text())
    original=[];altered=[];truncated=[]
    for row,symbol in zip(receipts,['^TNX','DX-Y.NYB'],strict=True):
        source=parse_yahoo_macro(Path(row['path']).read_bytes(),symbol=symbol,observed_at=datetime.fromisoformat(row['observed_at']))
        def known(q):return datetime.combine(q.session_day+timedelta(days=1),datetime.min.time(),UTC)+timedelta(hours=6)<=at
        original.append(source)
        altered.append(replace(source,quotes=tuple(replace(q,close=q.close*99) if not known(q) and q.close is not None else q for q in source.quotes)))
        truncated.append(replace(source,quotes=tuple(q for q in source.quotes if known(q))))
    result=macro_headwind_at(*original,at=at,clock=MacroClock.NEXT_DAY_06UTC_MODEL)
    assert result['status']=='MODEL_ONLY'
    assert result==macro_headwind_at(*altered,at=at,clock=MacroClock.NEXT_DAY_06UTC_MODEL)
    assert result==macro_headwind_at(*truncated,at=at,clock=MacroClock.NEXT_DAY_06UTC_MODEL)
