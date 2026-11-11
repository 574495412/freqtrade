"""Read-only authenticated Binance checks. This module cannot place or cancel orders."""

import hashlib
import hmac
import json
import os
import shlex
import stat
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import requests


ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "user_data/deployment/live-prepare-20261109"
ENDPOINTS = {
    "permissions": ("https://api.binance.com", "/sapi/v1/account/apiRestrictions"),
    "futures_account": ("https://fapi.binance.com", "/fapi/v3/account"),
    "futures_open_orders": ("https://fapi.binance.com", "/fapi/v1/openOrders"),
    "futures_config": ("https://fapi.binance.com", "/fapi/v1/accountConfig"),
    "copytrading_status": ("https://api.binance.com", "/sapi/v1/copyTrading/futures/userStatus"),
    "lead_symbols": ("https://api.binance.com", "/sapi/v1/copyTrading/futures/leadSymbol"),
}


def private_env(path):
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("Credential file must be a private regular file")
    result = {}
    for line in path.read_text().splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("export "):
            text = text[7:].strip()
        name, value = text.split("=", 1)
        parts = shlex.split(value)
        if len(parts) != 1:
            raise ValueError("Malformed private environment entry")
        result[name.strip()] = parts[0]
    return result


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)


def main():
    # The relay imports only paths and private_env. Keep the exchange engine out
    # of that long-running, read-only notification process.
    from freqtrade.exchange.request_guard import GuardedSession

    os.umask(0o077)
    STATE.mkdir(exist_ok=True)
    credential = private_env(ROOT / "user_data/deployment/.env.mainnet")
    key, secret = (
        credential[name]
        for name in ("BINANCE_COPYTRADING_API_KEY", "BINANCE_COPYTRADING_API_SECRET")
    )
    session = GuardedSession()
    session.auth = lambda request: request
    session.proxies.update(http="http://127.0.0.1:7897", https="http://127.0.0.1:7897")
    results = {}
    for name, (host, path) in ENDPOINTS.items():
        clock_path = "/api/v3/time" if host == "https://api.binance.com" else "/fapi/v1/time"
        try:
            clock = session.get(host + clock_path, timeout=10)
            clock.raise_for_status()
            params = {"timestamp": clock.json()["serverTime"], "recvWindow": 5000}
            query = urlencode(params)
            signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            # GET-only allowlist; never persist authenticated headers or signed query strings.
            response = session.get(
                host + path + "?" + query + "&signature=" + signature,
                headers={"X-MBX-APIKEY": key},
                timeout=15,
            )
            body = response.json()
            save(
                STATE / (name + ".private.json"),
                {
                    "endpoint": host + path,
                    "http_status": response.status_code,
                    "received_at_utc": datetime.now(UTC).isoformat(),
                    "body": body,
                },
            )
            results[name] = {"http_status": response.status_code, "ok": response.status_code == 200}
            if response.status_code != 200:
                results[name]["exchange_code"] = (
                    body.get("code") if isinstance(body, dict) else None
                )
            elif name == "permissions":
                results[name]["permissions"] = {
                    k: body.get(k)
                    for k in (
                        "enableReading",
                        "enableSpotAndMarginTrading",
                        "enableFutures",
                        "enableWithdrawals",
                        "ipRestrict",
                    )
                }
            elif name == "futures_account":
                usdt = next((a for a in body.get("assets", []) if a["asset"] == "USDT"), {})
                results[name].update(
                    can_trade=body.get("canTrade"),
                    available_usdt=usdt.get("availableBalance", "0"),
                    nonzero_positions=sum(
                        float(p.get("positionAmt", 0)) != 0 for p in body.get("positions", [])
                    ),
                )
            elif name == "futures_open_orders":
                results[name]["open_orders"] = len(body) if isinstance(body, list) else None
            elif name == "futures_config":
                results[name].update(
                    can_trade=body.get("canTrade"),
                    hedge_mode=body.get("dualSidePosition"),
                    multi_assets_margin=body.get("multiAssetsMargin"),
                )
            elif name == "copytrading_status":
                results[name].update(
                    exchange_code=body.get("code"),
                    is_lead_trader=(body.get("data") or {}).get("isLeadTrader"),
                )
            elif name == "lead_symbols":
                results[name].update(
                    exchange_code=body.get("code"),
                    symbol_count=len(body.get("data") or []),
                )
        except requests.RequestException as exc:
            results[name] = {"ok": False, "transport_error_type": type(exc).__name__}
        except (ValueError, KeyError):
            results[name] = {"ok": False, "response_error": "unrecognized_response"}
        print(json.dumps({"check": name, **results[name]}, ensure_ascii=False), flush=True)
    save(
        STATE / "account_readiness.json",
        {
            "checked_at_utc": datetime.now(UTC).isoformat(),
            "checks": results,
            "methods_used": ["GET"],
            "orders_submitted": 0,
            "orders_canceled": 0,
            "transfers": 0,
            "live_worker_started": False,
        },
    )


if __name__ == "__main__":
    main()
