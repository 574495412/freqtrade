"""Current local integration against all preceding equity component checks."""
import pytest
from bootstrap import HERE, ROOT, boot

boot()
paths = [HERE / 'test_open_only.py']
for directory, name in [
    ('session_routing_native_20261115_v1', 'test_sessions.py'),
    ('cash_dividends_native_20261114_v1', 'test_dividends.py'),
    ('net_nav_native_20261114_v1', 'test_nav.py'),
    ('dated_fees_native_20261114_v1', 'test_fees.py'),
    ('historical_execution_native_20261114_v1', 'test_execution.py'),
    ('settled_cash_native_20261114_v1', 'test_settlement.py'),
]:
    paths.append(HERE.parent / directory / name)
paths += [ROOT / 'tests/data/history/test_equity.py',
          ROOT / 'tests/data/test_equity_provider.py', ROOT / 'tests/test_equity_runtime.py']
raise SystemExit(pytest.main(['-q', '--noconftest', *map(str, paths)]))
