"""Native custom-state parity, including updates, deletion and run resets."""

import pytest

from freqtrade.persistence.custom_data import CustomDataWrapper
from research.crypto.indexed_custom_data import indexed_backtest_custom_data


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(CustomDataWrapper, "use_db", False)
    monkeypatch.setattr(CustomDataWrapper, "custom_data", [])


def values(getter, trade_id, key=None):
    return [
        (entry.ft_trade_id, entry.cd_key, entry.cd_type, entry.cd_value, entry.value)
        for entry in getter(trade_id=trade_id, key=key)
    ]


def test_index_matches_native_queries_updates_deletes_and_resets(offline):
    original = CustomDataWrapper.get_custom_data
    CustomDataWrapper.set_custom_data(1, "Stop", 98.3)
    CustomDataWrapper.set_custom_data(1, "Risk", {"distance": 0.02})
    CustomDataWrapper.set_custom_data(2, "Stop", 50.0)
    with indexed_backtest_custom_data():
        getter = CustomDataWrapper.get_custom_data
        for trade_id in [1, 2, 99]:
            for key in [None, "stop", "STOP", "risk", "missing"]:
                assert values(getter, trade_id, key) == values(original, trade_id, key)
        CustomDataWrapper.set_custom_data(1, "sTOP", 99.0)
        CustomDataWrapper.set_custom_data(3, "Stop", 70.0)
        assert values(getter, 1) == values(original, 1)
        assert values(getter, 3) == values(original, 3)
        CustomDataWrapper.delete_custom_data(1, "Risk")
        assert values(getter, 1) == values(original, 1)
        CustomDataWrapper.delete_custom_data(2)
        assert values(getter, 2) == []
        CustomDataWrapper.reset_custom_data()
        CustomDataWrapper.set_custom_data(1, "Stop", 42.0)
        assert values(getter, 1) == values(original, 1)
        assert values(getter, 3) == []
    assert CustomDataWrapper.get_custom_data is original


def test_database_mode_delegates_to_unmodified_native_getter(monkeypatch):
    monkeypatch.setattr(CustomDataWrapper, "use_db", True)
    sentinel = object()
    calls = []

    def database_getter(**kwargs):
        calls.append(kwargs)
        return sentinel

    monkeypatch.setattr(CustomDataWrapper, "get_custom_data", staticmethod(database_getter))
    with indexed_backtest_custom_data():
        assert CustomDataWrapper.get_custom_data(trade_id=7, key="R") is sentinel
    assert calls == [{"trade_id": 7, "key": "R"}]


def test_index_restores_native_method_after_exception(offline):
    original = CustomDataWrapper.get_custom_data
    with pytest.raises(RuntimeError), indexed_backtest_custom_data():
        raise RuntimeError("study failed")
    assert CustomDataWrapper.get_custom_data is original
