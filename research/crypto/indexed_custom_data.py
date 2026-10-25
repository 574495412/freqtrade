"""Research-only index for the native append/update/reset/delete custom-state API."""

from collections import defaultdict
from contextlib import contextmanager

from freqtrade.persistence.custom_data import CustomDataWrapper


@contextmanager
def indexed_backtest_custom_data():
    original = CustomDataWrapper.get_custom_data
    source, length, indexed = None, 0, defaultdict(list)

    def getter(*, trade_id, key=None):
        nonlocal source, length, indexed
        if CustomDataWrapper.use_db:
            return original(trade_id=trade_id, key=key)
        current = CustomDataWrapper.custom_data
        if current is not source or len(current) < length:
            source, length, indexed = current, 0, defaultdict(list)
        if len(current) > length:
            for entry in current[length:]:
                indexed[entry.ft_trade_id].append(entry)
            length = len(current)
        records = indexed.get(trade_id, [])
        if key is not None:
            records = [entry for entry in records if entry.cd_key.casefold() == key.casefold()]
        return [CustomDataWrapper._convert_custom_data(entry) for entry in records]

    CustomDataWrapper.get_custom_data = staticmethod(getter)
    try:
        yield
    finally:
        CustomDataWrapper.get_custom_data = staticmethod(original)
