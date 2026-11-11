"""The notification daemon must not load the exchange or numerical engine."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module",
    ["deploy.live_prepare_20261109.notify_relay", "deploy.mainnet_100_20261109.live_selector"],
)
def test_auxiliary_daemon_stays_independent_of_heavy_trading_imports(module):
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                f"import sys; import {module}; "
                "assert not {'ccxt', 'pandas', 'numpy', 'freqtrade.exchange'} & set(sys.modules)"
            ),
        ],
        check=True,
        timeout=15,
    )
