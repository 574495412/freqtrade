"""Lightweight auxiliary GET client sharing the exact native request budget."""

import json
from pathlib import Path
from urllib.parse import urlparse

import requests

from freqtrade.request_guard_core import SharedCooldown, SharedRequestGuard


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "user_data/deployment/binance-request-guard.json"

def configured_guard():
    if not POLICY.exists():
        return None
    if POLICY.is_symlink() or POLICY.stat().st_mode & 0o077:
        raise ValueError("Binance guard policy must be private")
    policy = json.loads(POLICY.read_text())
    if policy.get("enabled") is not True:
        raise ValueError("Existing Binance guard policy cannot silently disable protection")
    return SharedRequestGuard(
        ROOT / "user_data/deployment/binance-request-guard.sqlite",
        weight_per_minute=policy["weight_per_minute"],
        requests_per_minute=policy["requests_per_minute"],
        in_flight=policy["in_flight"],
    )


class GuardedSession(requests.Session):
    """Guard auxiliary GET readers too; never admits a Binance trading write.

    Diagnostic waits are bounded to two seconds because their caller may already
    have signed the URL. Scheduled public input preparation can opt into waiting.
    """

    rate_error = SharedCooldown

    def make_guard(self):
        return configured_guard()

    def __init__(self, *, max_wait=2):
        super().__init__()
        self.guard = self.make_guard()
        self.guard_max_wait = max_wait

    def request(self, method, url, **kwargs):
        parsed = urlparse(url)
        if not self.guard or parsed.hostname not in {
            "api.binance.com",
            "fapi.binance.com",
            "dapi.binance.com",
        }:
            return super().request(method, url, **kwargs)
        if method.upper() != "GET":
            raise requests.RequestException("Auxiliary Binance session permits GET only")
        endpoint = parsed.path.rsplit("/", 1)[-1]
        weight = {
            "time": 1,
            "exchangeInfo": 20,
            "account": 20,
            "openOrders": 80,
            "apiRestrictions": 4,
            "myTrades": 20,
            "order": 4,
        }.get(endpoint, 80)
        if endpoint == "klines":
            if parsed.hostname == "api.binance.com":
                weight = 2
            else:
                limit = int((kwargs.get("params") or {}).get("limit", 500))
                weight = 1 if limit < 100 else 2 if limit < 500 else 5 if limit <= 1000 else 10
        try:
            ticket = self.guard.acquire(weight, max_wait=self.guard_max_wait)
        except self.rate_error:
            raise requests.RequestException(
                "Shared Binance request cooldown/budget active"
            ) from None
        try:
            self.guard.before_transport()
            response = super().request(method, url, **kwargs)
            self.guard.response(response.status_code, response.headers, response.text)
            return response
        except self.rate_error:
            raise requests.RequestException("Shared Binance request cooldown active") from None
        finally:
            self.guard.release(ticket)
