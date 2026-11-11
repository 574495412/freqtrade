"""Read-only spot lead-account checks and isolated STOPPED native configuration.

This local preparation tool never instantiates a trading client or submits orders.
"""

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlencode

import requests

from deploy.launch import verify
from deploy.live_prepare_20261109.notify_relay import PORT, initialize
from deploy.live_prepare_20261109.preflight import ROOT, private_env, save
from deploy.live_prepare_20261109.prepare import native_schema_check, webhook_config
from freqtrade.exchange.request_guard import GuardedSession


STATE = ROOT / "user_data/deployment/spot-live-prepare-20261109"
HOST = "https://api.binance.com"
READ_PATHS = {
    "permissions": "/sapi/v1/account/apiRestrictions",
    "account": "/api/v3/account",
    "open_orders": "/api/v3/openOrders",
}


def spot_credentials(values):
    # Intentionally no fallback to the futures key, including when only one field exists.
    key, secret = (
        values[name]
        for name in ("BINANCE_COPYTRADING_SPOT_API_KEY", "BINANCE_COPYTRADING_SPOT_API_SECRET")
    )
    if not key or not secret:
        raise ValueError("Dedicated spot credential is empty")
    return {"exchange": {"key": key, "secret": secret}}


def signed_read(session, name, credentials):
    path = READ_PATHS[name]
    clock = session.get(HOST + "/api/v3/time", timeout=10, allow_redirects=False)
    clock.raise_for_status()
    query = urlencode({"timestamp": clock.json()["serverTime"], "recvWindow": 5000})
    signature = hmac.new(
        credentials["secret"].encode(), query.encode(), hashlib.sha256
    ).hexdigest()
    response = session.get(
        HOST + path + "?" + query + "&signature=" + signature,
        headers={"X-MBX-APIKEY": credentials["key"]},
        timeout=15,
        allow_redirects=False,
    )
    # Do not expose a requests exception containing the signed URL.
    if response.status_code != 200:
        raise ValueError(f"Spot {name} read failed: HTTP {response.status_code}")
    return response.json()


def pair_blockers(pairs, account_permissions, exchange_info):
    lookup = {symbol["symbol"]: symbol for symbol in exchange_info["symbols"]}
    rejected = []
    for pair in pairs:
        symbol = lookup.get(pair.replace("/", ""), {})
        groups = symbol.get("permissionSets")
        if (
            pair.count("/") != 1
            or not pair.endswith("/USDT")
            or symbol.get("quoteAsset") != "USDT"
            or symbol.get("status") != "TRADING"
            or symbol.get("isSpotTradingAllowed") is not True
            or "LIMIT" not in symbol.get("orderTypes", [])
            or not groups
            or not all(set(group) & set(account_permissions) for group in groups)
        ):
            rejected.append(pair)
    return rejected


def account_blockers(permissions, account, orders, rejected):
    blockers = []
    if permissions.get("enableSpotAndMarginTrading") is not True:
        blockers.append("SPOT_KEY_TRADE_PERMISSION_DISABLED")
    if account.get("canTrade") is not True:
        blockers.append("ACCOUNT_TRADING_NOT_CONFIRMED")
    usdt = next((a for a in account.get("balances", []) if a["asset"] == "USDT"), {})
    if Decimal(usdt.get("free", "0")) <= 0:
        blockers.append("NO_AVAILABLE_SPOT_USDT")
    if orders or any(
        Decimal(asset["locked"]) != 0
        or (asset["asset"] != "USDT" and Decimal(asset["free"]) != 0)
        for asset in account.get("balances", [])
    ):
        blockers.append("EXISTING_ACCOUNT_STATE_REQUIRES_RECONCILIATION")
    if rejected:
        blockers.append("UNSUPPORTED_CONFIGURED_SPOT_PAIRS")
    return blockers


def main():
    verify()
    os.umask(0o077)
    STATE.mkdir(mode=0o700, exist_ok=True)
    if (STATE / "trades.sqlite").exists():
        raise ValueError("Existing live ledger: refusing to overwrite preparation")
    credentials = spot_credentials(private_env(ROOT / "user_data/deployment/.env.mainnet"))
    config = json.loads((ROOT / "deploy/configs/spot.json").read_text())
    session = GuardedSession()
    session.auth = lambda request: request
    session.trust_env = False
    proxy = config["exchange"]["ccxt_config"]["httpsProxy"]
    session.proxies.update(http=proxy, https=proxy)
    folder = STATE / ("checks-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"))
    folder.mkdir(mode=0o700)
    checks = {}
    for name in READ_PATHS:
        checks[name] = signed_read(session, name, credentials["exchange"])
        save(folder / (name + ".private.json"), checks[name])
    response = session.get(
        HOST + "/api/v3/exchangeInfo",
        params={
            "permissions": json.dumps(checks["account"]["permissions"]),
            "showPermissionSets": "true",
        },
        timeout=20,
        allow_redirects=False,
    )
    if response.status_code != 200:
        raise ValueError(f"Spot symbol read failed: HTTP {response.status_code}")
    info = response.json()
    save(folder / "exchange_info.private.json", info)
    rejected = pair_blockers(
        config["exchange"]["pair_whitelist"], checks["account"]["permissions"], info
    )
    blockers = account_blockers(
        checks["permissions"], checks["account"], checks["open_orders"], rejected
    )
    balance = next(a for a in checks["account"]["balances"] if a["asset"] == "USDT")
    free = Decimal(balance["free"])
    allocated = free * Decimal(str(config["tradable_balance_ratio"]))
    config.pop("dry_run_wallet", None)
    config.pop("fee", None)  # Native live mode must use exchange fees.
    config.update(
        dry_run=False,
        initial_state="stopped",
        force_entry_enable=False,
        bot_name="nfi-x8-spot-lead-live-prepared",
        # Native available_capital overrides tradable_balance_ratio, so reserve explicitly.
        # Closed trading PnL will change this budget; later wallet deposits will not.
        available_capital=float(allocated),
        db_url="sqlite:///" + str(STATE / "trades.sqlite"),
        strategy_path=str(ROOT / "deploy/strategy/nfi-x8"),
    )
    config["exchange"].update(key="", secret="", password="")
    config["api_server"]["enabled"] = False
    # Share transport only; futures credentials, configuration and ledger remain independent.
    config["webhook"] = webhook_config(
        "spot", f"http://127.0.0.1:{PORT}/events/" + initialize()
    )
    schema = native_schema_check(config)
    # Futures-only strategy defaults do not apply in spot mode.
    schema.pop("futures_leverage", None)
    schema["trading_mode"] = "spot"
    if blockers:
        raise ValueError("Spot preparation blocked: " + ", ".join(blockers))
    save(STATE / "config.json", config)
    save(STATE / "credentials.json", credentials)
    receipt = {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "credential_source_names": [
            "BINANCE_COPYTRADING_SPOT_API_KEY", "BINANCE_COPYTRADING_SPOT_API_SECRET"
        ],
        "market": "spot",
        "account_role": "spot_lead_trading_as_identified_by_user",
        "spot_trade_permission": checks["permissions"]["enableSpotAndMarginTrading"],
        "withdrawal_permission": checks["permissions"].get("enableWithdrawals"),
        "ip_restricted": checks["permissions"].get("ipRestrict"),
        "free_usdt": str(free),
        "initial_allocated_capital_usdt": str(allocated),
        "capital_policy": "Native available_capital plus closed PnL; later deposits excluded",
        "open_orders": len(checks["open_orders"]),
        "configured_pair_count": len(config["exchange"]["pair_whitelist"]),
        "exchange_permission_rejected_pairs": rejected,
        "pair_check_scope": "USDT quote, active spot LIMIT market and account permissionSets",
        "lead_specific_symbol_whitelist": "not independently verified by a dedicated endpoint",
        "native_schema": schema,
        "account_connectivity_blockers": blockers,
        "promotion_status": "unqualified; existing exit-time diagnostics unresolved",
        "initial_state": "stopped",
        "config_path": str(STATE / "config.json"),
        "credential_overlay_path": str(STATE / "credentials.json"),
        "check_artifacts": str(folder),
        "http_methods_used": ["GET"],
        "live_worker_started": False,
        "orders_submitted": 0,
        "transfers": 0,
    }
    save(STATE / "preparation.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as exc:
        raise SystemExit("Spot network check failed: " + type(exc).__name__) from None
