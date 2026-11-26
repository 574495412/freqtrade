from types import SimpleNamespace

import pytest
from CaptureRules import terminal_snapshot


def test_native_dict_of_lists_records_all_trades_without_mutating_it():
    trade=SimpleNamespace(pair='BTC/USDT',id=1,amount=.1,open_date_utc='2025-01-01',has_open_position=True)
    rows={'BTC/USDT':[trade],'ETH/USDT':[]}
    result=terminal_snapshot(rows)
    assert result[0]['trade_id']==1 and result[0]['has_open_position']
    assert rows['BTC/USDT']==[trade] and rows['ETH/USDT']==[]


def test_empty_native_terminal_state_remains_empty():
    assert terminal_snapshot({})==[]


def test_wrong_terminal_signature_is_rejected_before_reading_trade_fields():
    with pytest.raises(TypeError,match='dict'):
        terminal_snapshot(['BTC/USDT'])
