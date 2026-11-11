"""GET-only reconciliation before restoring existing native live ledgers."""

import hashlib
import hmac
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlencode

from deploy.mainnet_100_20261109.prepare import STATE
from deploy.nfi80_selected.selector import read, save
from freqtrade.exchange.request_guard import GuardedSession


def compare_positions(market, trades, account):
    expected = {}
    for trade in trades:
        symbol = trade["pair"].split(":")[0].replace("/", "")
        expected[symbol] = expected.get(symbol, Decimal(0)) + Decimal(str(trade["amount"])) * (
            -1 if trade["is_short"] else 1
        )
    if market == "futures":
        actual = {
            p["symbol"]: Decimal(p["positionAmt"])
            for p in account["positions"]
            if Decimal(p["positionAmt"])
        }
    else:
        actual = {
            b["asset"] + "USDT": Decimal(b["free"]) + Decimal(b["locked"])
            for b in account["balances"]
            if b["asset"] != "USDT" and Decimal(b["free"]) + Decimal(b["locked"])
        }
    expected = {k: v for k, v in expected.items() if v}
    if expected != actual:
        raise ValueError("Exchange position differs from native ledger; restore refused")
    return {k: str(v) for k, v in actual.items()}


def observe(market, folder):
    config = read(STATE / market / "config.json")
    credential = read(STATE / market / "credentials.json")["exchange"]
    futures = market == "futures"
    host = "https://fapi.binance.com" if futures else "https://api.binance.com"
    with GuardedSession(max_wait=15) as session:
        session.trust_env = False
        session.auth = lambda request: request
        proxy = config["exchange"]["ccxt_config"]["httpsProxy"]
        session.proxies.update(http=proxy, https=proxy)

        def get(path):
            # Each attempt gets a fresh server clock/signature and shared quota.
            # This helper never issues a write or retries an ambiguous write.
            clock = session.get(
                host + ("/fapi/v1/time" if futures else "/api/v3/time"),
                timeout=15,
                allow_redirects=False,
            )
            if clock.status_code != 200:
                raise ValueError("Read-only clock unavailable")
            params = {"timestamp": clock.json()["serverTime"], "recvWindow": 20000}
            query = urlencode(params)
            params["signature"] = hmac.new(
                credential["secret"].encode(), query.encode(), hashlib.sha256
            ).hexdigest()
            response = session.get(
                host + path,
                params=params,
                headers={"X-MBX-APIKEY": credential["key"]},
                timeout=20,
                allow_redirects=False,
            )
            if response.status_code != 200:
                raise ValueError("Read-only account HTTP " + str(response.status_code))
            value = response.json()
            save(folder / (market + "-" + path.rsplit("/", 1)[-1] + ".private.json"), value)
            return value

        account = get("/fapi/v3/account" if futures else "/api/v3/account")
        orders = get("/fapi/v1/openOrders" if futures else "/api/v3/openOrders")
        if futures:
            mode = get("/fapi/v1/accountConfig")
            if (
                mode.get("canTrade") is not True
                or mode.get("dualSidePosition") is not False
                or mode.get("multiAssetsMargin") is not False
            ):
                raise ValueError("Existing futures account mode changed")
    with sqlite3.connect(
        f"file:{(STATE / market / 'trades.sqlite').resolve()}?mode=ro", uri=True
    ) as connection:
        connection.row_factory = sqlite3.Row
        trades = [dict(r) for r in connection.execute("select * from trades where is_open=1")]
        native_open_orders = {
            r[0] for r in connection.execute("select order_id from orders where ft_is_open=1")
        }
    positions = compare_positions(market, trades, account)
    remote_ids = {str(o["orderId"]) for o in orders}
    if not native_open_orders <= remote_ids:
        raise ValueError("Open native orders absent at exchange; restore refused")
    if futures and remote_ids != native_open_orders:
        raise ValueError("Unowned futures orders require reconciliation")
    return {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "market": market,
        "positions_reconciled": positions,
        "native_open_trade_count": len(trades),
        "exchange_open_order_count": len(orders),
        "unowned_spot_orders_preserved": len(remote_ids - native_open_orders),
        "methods": ["GET"],
        "orders_submitted": 0,
        "orders_cancelled": 0,
    }
