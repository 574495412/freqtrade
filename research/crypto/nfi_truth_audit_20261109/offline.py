"""Pinned native Exchange metadata adapter for research, with outbound sockets blocked."""

import json
import socket
from pathlib import Path


def install(snapshot):
    from freqtrade.enums import RunMode
    from freqtrade.resolvers import ExchangeResolver

    data = json.loads(Path(snapshot).read_text())
    original = ExchangeResolver.load_exchange

    def load(config, *, exchange_config=None, validate=True, load_leverage_tiers=False):
        if config.get("dry_run") is not True or config["runmode"] in (
            RunMode.LIVE,
            RunMode.DRY_RUN,
        ):
            raise ValueError("Frozen metadata adapter is exclusively for historical research")
        exchange = original(
            config, exchange_config=exchange_config, validate=False, load_leverage_tiers=False
        )
        exchange._markets = data["markets"]
        exchange._leverage_tiers = data["leverage_tiers"]
        exchange._api.set_markets(data["markets"], data["currencies"])
        exchange._api_async.set_markets(data["markets"], data["currencies"])
        if validate:
            exchange.validate_config(config)
        return exchange

    def blocked(*args, **kwargs):
        raise RuntimeError("Historical research attempted outbound network access")

    ExchangeResolver.load_exchange = staticmethod(load)
    socket.socket.connect = blocked
