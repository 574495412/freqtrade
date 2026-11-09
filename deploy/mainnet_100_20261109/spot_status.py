"""Spot-only authenticated GET observation; never start a worker or create an order."""

import hashlib
import hmac
import json
import os
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

import requests

from deploy.mainnet_100_20261109.prepare import HERE, STATE
from deploy.nfi80_selected.selector import read, save, sha


READ_PATHS = {
    "permissions": "/sapi/v1/account/apiRestrictions",
    "account": "/api/v3/account",
    "open_orders": "/api/v3/openOrders",
}


def signed_read(session, name, credentials):
    path = READ_PATHS[name]
    clock = session.get("https://api.binance.com/api/v3/time", timeout=10, allow_redirects=False)
    if clock.status_code != 200:
        raise ValueError(f"Spot time GET failed: HTTP {clock.status_code}")
    query = urlencode({"timestamp": clock.json()["serverTime"], "recvWindow": 5000})
    signature = hmac.new(credentials["secret"].encode(), query.encode(), hashlib.sha256).hexdigest()
    response = session.get(
        "https://api.binance.com" + path + "?" + query + "&signature=" + signature,
        headers={"X-MBX-APIKEY": credentials["key"]},
        timeout=15,
        allow_redirects=False,
    )
    if response.status_code != 200:
        raise ValueError(f"Spot {name} GET failed: HTTP {response.status_code}")
    return response.json()


def pair_blockers(pairs, permissions, info):
    symbols = {s["symbol"]: s for s in info["symbols"]}
    rejected = []
    for pair in pairs:
        symbol = symbols.get(pair.replace("/", ""), {})
        groups = symbol.get("permissionSets")
        if (
            pair.count("/") != 1
            or not pair.endswith("/USDT")
            or symbol.get("quoteAsset") != "USDT"
            or symbol.get("status") != "TRADING"
            or symbol.get("isSpotTradingAllowed") is not True
            or "LIMIT" not in symbol.get("orderTypes", [])
            or not groups
            or not all(set(group) & set(permissions) for group in groups)
        ):
            rejected.append(pair)
    return rejected


def account_blockers(permissions, account, orders, rejected):
    blockers = []
    if (
        permissions.get("enableSpotAndMarginTrading") is not True
        or account.get("canTrade") is not True
    ):
        blockers.append("SPOT_TRADE_PERMISSION_NOT_CONFIRMED")
    if orders or any(
        Decimal(a["locked"]) != 0 or (a["asset"] != "USDT" and Decimal(a["free"]) != 0)
        for a in account.get("balances", [])
    ):
        blockers.append("ACCOUNT_STATE_REQUIRES_RECONCILIATION")
    if rejected:
        blockers.append("UNSUPPORTED_CONFIGURED_SPOT_PAIRS")
    return blockers


def observe():
    os.umask(0o077)
    spot = STATE / "spot"
    config = read(spot / "config.json")
    release = read(HERE / "release.json")
    if sha(spot / "config.json") != release["config_sha256"]["spot"]:
        raise ValueError("Spot runtime config changed")
    if (
        config["trading_mode"] != "spot"
        or config["dry_run"] is not False
        or config["available_capital"] != 99
        or config["capital_ceiling_usdt"] != 100
        or config["max_open_trades"] != 6
    ):
        raise ValueError("Unexpected spot capital/market contract")
    credentials = read(spot / "credentials.json")["exchange"]
    folder = STATE / ("spot-observe-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"))
    folder.mkdir(mode=0o700)
    with requests.Session() as session:
        session.auth = lambda request: request
        session.trust_env = False
        proxy = config["exchange"]["ccxt_config"]["httpsProxy"]
        session.proxies.update(http=proxy, https=proxy)
        payloads = {}
        for name in ("permissions", "account", "open_orders"):
            payloads[name] = signed_read(session, name, credentials)
            save(folder / (name + ".private.json"), payloads[name])
        response = session.get(
            "https://api.binance.com/api/v3/exchangeInfo",
            params={
                "symbols": json.dumps(
                    [p.replace("/", "") for p in config["exchange"]["pair_whitelist"]],
                    separators=(",", ":"),
                ),
                "showPermissionSets": "true",
            },
            timeout=20,
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise ValueError(f"Spot exchangeInfo GET failed: HTTP {response.status_code}")
        info = response.json()
        save(folder / "exchange_info.private.json", info)
    rejected = pair_blockers(
        config["exchange"]["pair_whitelist"], payloads["account"]["permissions"], info
    )
    blockers = account_blockers(
        payloads["permissions"], payloads["account"], payloads["open_orders"], rejected
    )
    account = payloads["account"]
    free = next(Decimal(a["free"]) for a in account["balances"] if a["asset"] == "USDT")
    if free < 100:
        blockers.append("LESS_THAN100_USDT_FREE")
    if payloads["permissions"].get("enableWithdrawals") is not False:
        blockers.append("WITHDRAWAL_PERMISSION_NOT_DISABLED")
    with sqlite3.connect(
        f"file:{(spot / 'trades.sqlite').resolve()}?mode=ro", uri=True
    ) as connection:
        counts = {
            k: connection.execute(sql).fetchone()[0]
            for k, sql in {
                "trades": "select count(*) from trades",
                "orders": "select count(*) from orders",
                "open_trades": "select count(*) from trades where is_open=1",
            }.items()
        }
    # This observer certifies a flat-state snapshot only. An active ledger needs
    # native per-order/fill reconciliation, never an automatic reset or liquidation.
    flat = not counts["trades"] and not counts["orders"] and not blockers
    receipt = {
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "market": "spot",
        "configured_pair_count": len(config["exchange"]["pair_whitelist"]),
        "rejected_pairs": rejected,
        "configured_slots": 6,
        "capital_ceiling_usdt": 100,
        "stake_budget_usdt": 99,
        "available_funds_cover100_usdt": free >= 100,
        "account_can_trade": account.get("canTrade"),
        "api_spot_trade_permission": payloads["permissions"].get("enableSpotAndMarginTrading"),
        "withdrawal_disabled": payloads["permissions"].get("enableWithdrawals") is False,
        "account_commission_rates": account.get("commissionRates"),
        "commission_scope": (
            "Account base rates only; symbol promotions and actual charged fees unverified"
        ),
        "account_open_orders": len(payloads["open_orders"]),
        "local_ledger": counts,
        "blockers": blockers,
        "flat_state_snapshot_reconciled": flat,
        "lead_specific_symbol_whitelist": "unverified_by_dedicated_official_endpoint",
        "lead_account_role": "user_identified_not_independently_certified",
        "actual_fill_execution_verified": False,
        "methods": ["GET"],
        "orders_submitted": 0,
        "orders_cancelled": 0,
        "transfers": 0,
        "workers_started": 0,
        "private_artifacts": str(folder),
        "source_sha256": sha(Path(__file__)),
    }
    save(HERE / "spot-runtime-check.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return receipt


if __name__ == "__main__":
    try:
        observe()
    except Exception as exc:
        raise SystemExit("Spot GET observation failed: " + type(exc).__name__) from None
