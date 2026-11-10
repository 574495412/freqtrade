"""GET-only reconciliation of native spot orders and exchange fills; never trade."""

import argparse
import hashlib
import hmac
import json
import os
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlencode

from deploy.mainnet_100_20261109.prepare import HERE, STATE
from deploy.mainnet_100_20261109.spot_status import signed_read as account_read
from deploy.nfi80_selected.selector import read, save, sha
from freqtrade.exchange.request_guard import GuardedSession


D = Decimal


def order_read(session, kind, credentials, symbol, order_id):
    paths = {"order_query": "/api/v3/order", "fills_query": "/api/v3/myTrades"}
    path = paths[kind]
    if not symbol.isalnum() or not symbol.endswith("USDT") or not str(order_id).isdigit():
        raise ValueError("Invalid native spot order identity")
    clock = session.get("https://api.binance.com/api/v3/time", timeout=10, allow_redirects=False)
    if clock.status_code != 200:
        raise ValueError("Spot clock GET failed")
    params = {
        "symbol": symbol,
        "orderId": str(order_id),
        "timestamp": clock.json()["serverTime"],
        "recvWindow": 5000,
    }
    if kind == "fills_query":
        params["limit"] = 1000
    query = urlencode(params)
    signature = hmac.new(credentials["secret"].encode(), query.encode(), hashlib.sha256).hexdigest()
    response = session.get(
        "https://api.binance.com" + path + "?" + query + "&signature=" + signature,
        headers={"X-MBX-APIKEY": credentials["key"]},
        timeout=15,
        allow_redirects=False,
    )
    if response.status_code != 200:
        raise ValueError(f"Spot order GET failed: HTTP{response.status_code}")
    return response.json()


def fill_totals(symbol, oid, base, side, fills):
    problems = []
    seen = set()
    fees = defaultdict(Decimal)
    qty = D(0)
    quote = D(0)
    net_base = D(0)
    if len(fills) >= 1000:
        problems.append("FILL_PAGE_LIMIT_REQUIRES_CONTINUATION")
    for fill in fills:
        if str(fill.get("orderId")) != oid or fill.get("symbol") != symbol:
            problems.append("FILL_IDENTITY_MISMATCH")
        key = (fill.get("symbol"), fill.get("id"))
        if key in seen:
            problems.append("DUPLICATE_TRADE_ID")
        seen.add(key)
        if bool(fill["isBuyer"]) != (side == "BUY"):
            problems.append("FILL_SIDE_MISMATCH")
        amount = D(str(fill["qty"]))
        value = D(str(fill["quoteQty"]))
        commission = D(str(fill["commission"]))
        qty += amount
        quote += value
        fees[fill["commissionAsset"]] += commission
        net_base += amount if fill["isBuyer"] else -amount
        if fill["commissionAsset"] == base:
            net_base -= commission
    return qty, quote, net_base, fees, problems


def reconcile_order(local, remote, fills):
    symbol = local["ft_pair"].replace("/", "")
    oid = str(local["order_id"])
    problems = []
    if str(remote.get("orderId")) != oid or remote.get("symbol") != symbol:
        problems.append("ORDER_IDENTITY_MISMATCH")
    if local["side"].upper() != remote["side"]:
        problems.append("ORDER_SIDE_MISMATCH")
    base = local["ft_pair"].split("/")[0]
    qty, quote, net_base, fees, fill_problems = fill_totals(
        symbol, oid, base, remote["side"], fills
    )
    problems.extend(fill_problems)
    tolerance = D("1e-10") * max(D(1), qty)
    if abs(qty - D(str(remote["executedQty"]))) > tolerance:
        problems.append("INCOMPLETE_EXCHANGE_FILLS")
    if abs(quote - D(str(remote["cummulativeQuoteQty"]))) > D("0.000001"):
        problems.append("EXCHANGE_QUOTE_SUM_MISMATCH")
    if abs(D(str(local["filled"] or 0)) - qty) > tolerance:
        problems.append("NATIVE_FILLED_QTY_MISMATCH")
    if qty and (local["cost"] is None or abs(D(str(local["cost"])) - quote) > D("0.000001")):
        problems.append("NATIVE_QUOTE_COST_MISMATCH")
    if local.get("ft_fee_base") is not None:
        if abs(D(str(local["ft_fee_base"])) - fees.get(base, D(0))) > tolerance:
            problems.append("NATIVE_BASE_FEE_MISMATCH")
    elif fees.get(base, D(0)):
        problems.append("NATIVE_BASE_FEE_RECORD_MISSING")
    return {
        "local_order_id": local["id"],
        "trade_id": local["ft_trade_id"],
        "symbol": symbol,
        "broker_order_id": oid,
        "exchange_status": remote["status"],
        "filled_qty": str(qty),
        "quote_turnover": str(quote),
        "commission_by_asset": {k: str(v) for k, v in fees.items()},
        "net_base_change_from_fills": str(net_base),
        "fill_count": len(fills),
        "native_fee_components_reconciled": all(
            asset == base or amount == 0 for asset, amount in fees.items()
        ),
        "passed": not problems,
        "problems": problems,
    }


def read_ledger(path):
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as c:
        c.row_factory = sqlite3.Row
        orders = [
            dict(r)
            for r in c.execute(
                "select id,ft_trade_id,ft_pair,order_id,status,side,amount,filled,cost,"
                "ft_fee_base,order_update_date from orders order by id"
            )
        ]
        trades = [dict(r) for r in c.execute("select id,pair,is_open,amount from trades")]
    return orders, trades


def position_differences(account, selected, trades, orders, results):
    position_diff = {}
    balances = {a["asset"]: D(a["free"]) + D(a["locked"]) for a in account["balances"]}
    expected = defaultdict(Decimal)
    for result in results:
        base = next(
            o["ft_pair"].split("/")[0] for o in selected if o["id"] == result["local_order_id"]
        )
        expected[base] += D(result["net_base_change_from_fills"])
    native = defaultdict(Decimal)
    for trade in trades:
        if trade["is_open"] and any(
            o["ft_trade_id"] == trade["id"] and D(str(o["filled"] or 0)) > 0 for o in orders
        ):
            native[trade["pair"].split("/")[0]] += D(str(trade["amount"]))
    for asset, value in expected.items():
        actual = balances.get(asset, D(0))
        tol = D("1e-10") * max(D(1), abs(value))
        if abs(actual - value) > tol or abs(native[asset] - actual) > tol:
            position_diff[asset] = {
                "from_fills": str(value),
                "account": str(actual),
                "native_open_trades": str(native[asset]),
            }
    for asset, amount in balances.items():
        if asset != "USDT" and asset not in expected and amount > D("1e-10"):
            position_diff[asset] = {
                "from_fills": "0",
                "account": str(amount),
                "native_open_trades": "0",
            }
    return position_diff


def observe(after_id=0):
    os.umask(0o077)
    spot = STATE / "spot"
    config = read(spot / "config.json")
    if (
        config["trading_mode"] != "spot"
        or config["dry_run"] is not False
        or config["capital_ceiling_usdt"] != 100
    ):
        raise ValueError("Expected existing authorized100USDT spot account")
    if sha(spot / "config.json") != read(HERE / "release.json")["config_sha256"]["spot"]:
        raise ValueError("Spot config digest changed")
    orders, trades = read_ledger(spot / "trades.sqlite")
    selected = [o for o in orders if o["id"] > after_id][:20]
    receipt = {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "local_orders": len(orders),
        "local_trades": len(trades),
        "orders_audited": 0,
        "actual_fill_execution_verified": False,
        "backtest_execution_parity_proven": False,
        "methods": ["GET"],
        "orders_submitted": 0,
        "orders_cancelled": 0,
        "workers_started": 0,
        "position_reconciled": False,
    }
    if not selected:
        receipt.update(
            status="WAITING_FOR_FIRST_NATURAL_ORDER" if not orders else "NO_NEW_LOCAL_ORDERS",
            orders=[],
            exchange_order_queries=0,
        )
        save(HERE / "spot-fill-reconciliation.json", receipt)
        print(json.dumps(receipt, indent=2))
        return receipt
    folder = STATE / ("spot-fill-audit-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"))
    folder.mkdir(mode=0o700)
    credentials = read(spot / "credentials.json")["exchange"]
    results = []
    with GuardedSession() as session:
        session.trust_env = False
        session.auth = lambda request: request
        proxy = config["exchange"]["ccxt_config"]["httpsProxy"]
        session.proxies.update(http=proxy, https=proxy)
        for local in selected:
            symbol = local["ft_pair"].replace("/", "")
            oid = local["order_id"]
            remote = order_read(session, "order_query", credentials, symbol, oid)
            fills = order_read(session, "fills_query", credentials, symbol, oid)
            save(folder / f"{local['id']}-order.private.json", remote)
            save(folder / f"{local['id']}-fills.private.json", fills)
            results.append(reconcile_order(local, remote, fills))
        account = account_read(session, "account", credentials)
        save(folder / "account.private.json", account)
    after, after_trades = read_ledger(spot / "trades.sqlite")
    raced = after != orders or after_trades != trades
    complete = after_id == 0 and len(selected) == len(orders)
    position_diff = {}
    if complete and not raced and all(r["passed"] for r in results):
        # Initial account inventory was authenticated as zero. Any non-strategy
        # transfer or manual trade remains a discrepancy, never silently reset.
        position_diff = position_differences(account, selected, trades, orders, results)
        receipt["position_reconciled"] = not position_diff
    order_position_verified = (
        complete
        and not raced
        and receipt["position_reconciled"]
        and all(r["passed"] for r in results)
        and any(D(r["filled_qty"]) > 0 for r in results)
    )
    fees_verified = bool(results) and all(r["native_fee_components_reconciled"] for r in results)
    verified = order_position_verified and fees_verified
    receipt.update(
        status="FILLS_RECONCILED"
        if verified
        else "RACED_RETRY_LATER"
        if raced
        else "PENDING_OR_INCOMPLETE_RECONCILIATION",
        orders=results,
        orders_audited=len(results),
        remaining_orders=max(0, len([o for o in orders if o["id"] > after_id]) - len(selected)),
        full_account_order_set_audited=complete,
        ledger_changed_during_reads=raced,
        actual_fill_execution_verified=verified,
        order_quantities_and_positions_verified=order_position_verified,
        all_fee_components_reconciled_to_native=fees_verified,
        position_discrepancies=position_diff,
        private_artifacts=str(folder),
        notification_order_id_linkage_supported=False,
        fee_scope=(
            "Official fees by currency. Native base-asset fee fields are checked; "
            "quote/third-asset fee valuation needs cycle-level reconciliation."
        ),
    )
    save(folder / "reconciliation.private.json", receipt)
    public = {k: v for k, v in receipt.items() if k not in ("orders", "position_discrepancies")}
    public["order_discrepancy_count"] = sum(not r["passed"] for r in results)
    public["position_discrepancy_assets"] = list(position_diff)
    public["detailed_order_ids_fills_and_balances_private"] = True
    save(HERE / "spot-fill-reconciliation.json", public)
    print(json.dumps(public, indent=2))
    return public


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--after-id", type=int, default=0)
    args = parser.parse_args()
    try:
        observe(args.after_id)
    except Exception as exc:
        save(
            HERE / "spot-fill-reconciliation.json",
            {
                "checked_at_utc": datetime.now(UTC).isoformat(),
                "status": "READ_FAILED",
                "error_type": type(exc).__name__,
                "actual_fill_execution_verified": False,
                "methods": ["GET"],
                "orders_submitted": 0,
            },
        )
        raise SystemExit("Spot fill observation stopped: " + type(exc).__name__) from None
