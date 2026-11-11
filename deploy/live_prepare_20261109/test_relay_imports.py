"""The notification daemon must not load the exchange or numerical engine."""

import subprocess
import sys


def test_relay_stays_independent_of_heavy_trading_imports():
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import deploy.live_prepare_20261109.notify_relay; "
                "assert not {'ccxt', 'pandas', 'numpy', 'freqtrade.exchange'} & set(sys.modules)"
            ),
        ],
        check=True,
        timeout=15,
    )
