"""GET-only bounded selector verification without changing the consumer snapshot."""

import argparse
import json
import resource
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import requests


@contextmanager
def readonly_public_transport():
    request = requests.Session.request
    counters = {"get_calls": 0, "blocked_writes": 0}

    def guarded(session, method, url, *args, **kwargs):
        parsed = urlparse(url)
        if (
            method.upper() != "GET"
            or parsed.hostname != "fapi.binance.com"
            or parsed.path not in ["/fapi/v1/exchangeInfo", "/fapi/v1/klines"]
        ):
            counters["blocked_writes"] += 1
            raise ValueError("Selector probe permits only public metadata/closed candles")
        counters["get_calls"] += 1
        return request(session, method, url, *args, **kwargs)

    requests.Session.request = guarded
    try:
        yield counters
    finally:
        requests.Session.request = request


def main():
    from deploy.aliyun_20261111.compute import selector_slot
    from deploy.nfi80_selected.selector import light_main, save

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    started = time.monotonic()
    with selector_slot(wait_seconds=90) as reason:
        if reason:
            print(json.dumps({"deferred": reason}), flush=True)
            raise SystemExit(75)
        with readonly_public_transport() as counters:
            result = light_main(
                force_refresh=True,
                publish=False,
                now=datetime.fromisoformat(args.day).replace(tzinfo=UTC),
            )
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result.update(
        elapsed_seconds=round(time.monotonic() - started, 3),
        transport=counters,
        max_rss_bytes=rss * (1024 if sys.platform == "linux" else 1),
    )
    save(Path(args.receipt), result)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
