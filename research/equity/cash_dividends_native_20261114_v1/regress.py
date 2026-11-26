"""Run current extensions against prior component checks, without altering receipts."""
from pathlib import Path
import pytest
from bootstrap import boot
boot()
h=Path(__file__).resolve().parent
paths=[h/'test_dividends.py']
for d, name in [('net_nav','test_nav.py'),('dated_fees','test_fees.py'),('historical_execution','test_execution.py'),('settled_cash','test_settlement.py')]:
 paths.append(h.parent/f'{d}_native_20261114_v1'/name)
raise SystemExit(pytest.main(['-q','--confcutdir='+str(h.parent),*[str(x)for x in paths]]))
