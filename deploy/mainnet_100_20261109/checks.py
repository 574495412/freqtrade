"""GET-only authenticated admission reads; never place/cancel orders or transfer."""

import hashlib
import hmac
import time
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlencode

import requests

from deploy.mainnet_100_20261109.prepare import HERE, STATE
from deploy.nfi80_selected.selector import read, save
from freqtrade.exchange.request_guard import GuardedSession


def futures_compatible(permissions, account, orders, mode, leader):
    # V3 balances omit canTrade; the separate accountConfig endpoint owns it.
    return (
        mode.get("canTrade") is True
        and account.get("canTrade", True) is True
        and permissions.get("enableFutures") is True
        and not orders
        and not any(Decimal(p["positionAmt"]) != 0 for p in account.get("positions", []))
        and mode.get("dualSidePosition") is False
        and mode.get("multiAssetsMargin") is False
        and (leader.get("data") or {}).get("isLeadTrader") is True
    )


def account_checks():  # noqa: C901 -- explicit, separate spot/futures admission rules
    folder = STATE / ("checks-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"))
    folder.mkdir(mode=0o700)
    results = {}
    for market in ("spot", "futures"):
        credential = read(STATE / market / "credentials.json")["exchange"]
        config = read(STATE / market / "config.json")
        with GuardedSession() as session:
            session.trust_env = False
            session.proxies.update(http="http://127.0.0.1:7897", https="http://127.0.0.1:7897")

            def once(
                host,
                path,
                signed=True,
                params=None,
                credential=credential,
                market=market,
                session=session,
            ):
                params = dict(params or {})
                headers = {}
                if signed:
                    clock_path = "/fapi/v1/time" if "fapi." in host else "/api/v3/time"
                    clock = session.get(host + clock_path, timeout=15, allow_redirects=False)
                    clock.raise_for_status()
                    params.update(timestamp=clock.json()["serverTime"], recvWindow=5000)
                    query = urlencode(params)
                    params["signature"] = hmac.new(
                        credential["secret"].encode(), query.encode(), hashlib.sha256
                    ).hexdigest()
                    headers["X-MBX-APIKEY"] = credential["key"]
                response = session.get(
                    host + path, params=params, headers=headers, timeout=20, allow_redirects=False
                )
                if response.status_code != 200:
                    raise ValueError(f"{market} GET check failed: HTTP{response.status_code}")
                body = response.json()
                save(folder / (market + "-" + path.rsplit("/", 1)[-1] + ".private.json"), body)
                return body

            def get(host, path, signed=True, params=None, request=once):
                for attempt in range(3):
                    try:
                        return request(host, path, signed, params)
                    except (requests.ConnectionError, requests.Timeout):
                        if attempt == 2:
                            raise
                        time.sleep(0.5 * (attempt + 1))

            permissions = get("https://api.binance.com", "/sapi/v1/account/apiRestrictions")
            if permissions.get("enableWithdrawals") is not False:
                raise ValueError("Withdrawal permission must be disabled")
            if market == "spot":
                account = get("https://api.binance.com", "/api/v3/account")
                orders = get("https://api.binance.com", "/api/v3/openOrders")
                free = next(Decimal(v["free"]) for v in account["balances"] if v["asset"] == "USDT")
                if (
                    account.get("canTrade") is not True
                    or permissions.get("enableSpotAndMarginTrading") is not True
                    or orders
                    or any(
                        Decimal(a["locked"]) != 0
                        or (a["asset"] != "USDT" and Decimal(a["free"]) != 0)
                        for a in account["balances"]
                    )
                ):
                    raise ValueError("Spot permissions or preexisting state require reconciliation")
                info = get(
                    "https://api.binance.com",
                    "/api/v3/exchangeInfo",
                    False,
                    {"showPermissionSets": "true"},
                )
                symbols = {s["symbol"]: s for s in info["symbols"]}
                for pair in config["exchange"]["pair_whitelist"]:
                    symbol = symbols.get(pair.replace("/", ""), {})
                    groups = symbol.get("permissionSets")
                    if (
                        symbol.get("status") != "TRADING"
                        or not symbol.get("isSpotTradingAllowed")
                        or "LIMIT" not in symbol.get("orderTypes", [])
                        or not groups
                        or not all(set(group) & set(account["permissions"]) for group in groups)
                    ):
                        raise ValueError("Spot pair not admitted by exchange/account permissions")
            else:
                account = get("https://fapi.binance.com", "/fapi/v3/account")
                orders = get("https://fapi.binance.com", "/fapi/v1/openOrders")
                mode = get("https://fapi.binance.com", "/fapi/v1/accountConfig")
                leader = get("https://api.binance.com", "/sapi/v1/copyTrading/futures/userStatus")
                symbols = get("https://api.binance.com", "/sapi/v1/copyTrading/futures/leadSymbol")
                free = next(
                    Decimal(v["availableBalance"])
                    for v in account["assets"]
                    if v["asset"] == "USDT"
                )
                if not futures_compatible(permissions, account, orders, mode, leader):
                    raise ValueError(
                        "Futures permissions, mode or existing state require reconciliation"
                    )
                admitted = {s["symbol"] for s in symbols.get("data", [])}
                from deploy.nfi80_selected.strategy.NFISelectedPaper import snapshot_pairs

                pool = snapshot_pairs(
                    config["daily_snapshot_file"], datetime.now(UTC), config["daily_policy_sha256"]
                )
                if len(pool) != 80 or any(
                    p.split(":")[0].replace("/", "") not in admitted for p in pool
                ):
                    raise ValueError("Fresh80-symbol lead-trading pool not admitted")
            if free < Decimal(100):
                raise ValueError(f"{market} has less than100USDT available")
            results[market] = {
                "free_usdt": str(free),
                "existing_orders": 0,
                "existing_positions_or_inventory": 0,
                "configured_cap_usdt": 100,
                "checks_passed": True,
            }
    receipt = {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "markets": results,
        "methods": ["GET"],
        "orders_submitted": 0,
        "transfers": 0,
        "live_workers_started": False,
    }
    save(HERE / "account-checks.json", receipt)
    return receipt
