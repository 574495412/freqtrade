"""Lightweight readers must share native budgets, cooldowns and admission failures."""
# ruff: noqa: S101

import subprocess
import sys
import time

from freqtrade.exchange.request_guard import SharedRequestGuard as NativeGuard
from freqtrade.request_guard_core import SharedCooldown, SharedRequestGuard


def test_light_guard_uses_the_same_native_leases_and_cooldown(tmp_path):
    path = tmp_path / "guard.sqlite"
    native = NativeGuard(path, requests_per_minute=60000, weight_per_minute=60000, in_flight=1)
    light = SharedRequestGuard(path, requests_per_minute=60000, weight_per_minute=60000, in_flight=1)
    ticket, _ = native.claim(1)
    assert ticket
    assert light.claim(1)[0] is None
    native.release(ticket)
    light.block_until(time.time() + 60, "test existing cooldown")
    assert native.status()["cooldown"] == light.status()["cooldown"]
    import pytest

    with pytest.raises(SharedCooldown):
        light.acquire(1, max_wait=0)
    assert native.status()["reason"] == "test existing cooldown"


def test_preflight_import_does_not_load_exchange_sdk_or_numerical_libraries():
    result = subprocess.run([
        sys.executable, "-B", "-c",
        "import sys; import deploy.runtime_recovery_20261111.preflight; "
        "assert not {'ccxt','pandas','freqtrade.exchange'} & sys.modules.keys()",
    ], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
