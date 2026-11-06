"""Offline Binance USD-M receipt reconciliation for the isolated AccountReplay."""

import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "rd_program_20261105/execution_extension")
)
from account_replay import dec, nanoseconds


CONTRACT = "binance_usdm_usdt_isolated_oneway_long_1x"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def uint(value):
    require(type(value) is int and 0 < value < 2**63, "positive integer ID or clock required")
    return value


def clock_text(ms):
    return (
        (EPOCH + timedelta(milliseconds=ms))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def amount(value):
    require(
        isinstance(value, str) and re.fullmatch(r"-?[0-9]{1,18}(\.[0-9]{1,9})?", value),
        "exact decimal string required",
    )
    return dec(value)


def label(value):
    require(isinstance(value, str) and value.strip() == value and value, "explicit ID required")
    return value


def fill(ws, rest, at, pair, campaign, orders):
    order = ws["o"]
    require(order["x"] == "TRADE", "unsupported order event")
    client_id = label(order["c"])
    require(
        order["o"] != "LIQUIDATION"
        and not client_id.startswith(("autoclose-", "adl_autoclose", "settlement_autoclose")),
        "liquidation, ADL and settlement fills need separate treatment",
    )
    side = order["S"]
    require(
        side in ("BUY", "SELL") and order["ps"] == rest["positionSide"] == "BOTH",
        "one-way fill required",
    )
    require(order["N"] == rest["commissionAsset"] == "USDT", "USDT commission required")
    if "marginAsset" in rest:
        require(rest["marginAsset"] == "USDT", "USDT margin required")

    trade_id, order_id = uint(order["t"]), uint(order["i"])
    size, price, fee, pnl = (amount(order[key]) for key in ("l", "L", "n", "rp"))
    require(size > 0 and price > 0 and fee >= 0, "invalid fill size, price or fee")
    require(
        order["s"] == rest["symbol"]
        and trade_id == uint(rest["id"])
        and order_id == uint(rest["orderId"])
        and at == uint(order["T"]) == uint(rest["time"])
        and side == rest["side"]
        and type(order["m"]) is bool
        and order["m"] is rest["maker"]
        and (size, price, fee, pnl)
        == tuple(amount(rest[key]) for key in ("qty", "price", "commission", "realizedPnl")),
        "WebSocket and REST fill conflict",
    )

    requested, cumulative = (amount(order[key]) for key in ("q", "z"))
    identity = (pair, order_id)
    previous_campaign, previous_side, previous_requested, previous_cumulative = orders.get(
        identity, (campaign, side, requested, 0)
    )
    require(
        (campaign, side, requested) == (previous_campaign, previous_side, previous_requested)
        and cumulative == previous_cumulative + size
        and cumulative <= requested
        and order["X"] == ("FILLED" if cumulative == requested else "PARTIALLY_FILLED"),
        "order fill chain is missing or inconsistent",
    )
    orders[identity] = (campaign, side, requested, cumulative)
    return {
        "event_id": f"fill:{order['s']}:{trade_id}",
        "kind": "fill",
        "order_id": str(order_id),
        "fill_id": str(trade_id),
        "signed_quantity": str(size if side == "BUY" else -size),
        "price": str(price),
        "fee_quote": str(fee),
        "reported_realized_pnl": str(pnl),
        "maker": order["m"],
    }


def funding(ws, rest, at):
    account = ws["a"]
    require(account["m"] == rest["incomeType"] == "FUNDING_FEE", "unsupported balance event")
    balances, positions = account["B"], account["P"]
    require(
        isinstance(balances, list)
        and isinstance(positions, list)
        and len(balances) == len(positions) == 1,
        "single-asset isolated funding receipt required",
    )
    balance, position = balances[0], positions[0]
    require(
        balance["a"] == rest["asset"] == "USDT"
        and position["mt"] == "isolated"
        and position["ps"] == "BOTH",
        "one-way isolated USDT funding required",
    )
    income = amount(rest["income"])
    require(
        position["s"] == rest["symbol"]
        and account.get("S", position["s"]) == position["s"]
        and at == uint(rest["time"])
        and income == amount(balance["bc"]),
        "WebSocket and REST funding conflict",
    )
    funding_id = f"funding:{uint(rest['tranId'])}"
    return {
        "event_id": funding_id,
        "kind": "funding_settlement",
        "funding_id": funding_id,
        "cost_quote": str(-income),
    }


def normalize_batch(bundle, *, pairs):
    """Match supplied redacted payloads, in the declared order, without side effects.

    pairs maps the allowed Binance symbols to the frozen research pairs. Return data
    are for retrospective arithmetic with sequence_basis='unknown'.
    """
    require(bundle.get("contract") == CONTRACT, "explicit isolated research contract required")
    evidence = bundle.get("evidence_class")
    require(evidence in ("synthetic", "captured"), "explicit evidence class required")
    account_id = label(bundle.get("account_id"))
    connection_id = label(bundle.get("connection_id"))
    rows = bundle.get("financial_receipts")
    require(isinstance(rows, list) and rows, "nonempty financial receipt list required")

    events, seen, orders = [], set(), {}
    by_kind, campaign_pairs, quantities, active = {}, {}, {}, {}
    last_at, last_received, first_received = 0, 0, None
    for sequence, row in enumerate(rows, 1):
        try:
            require(uint(row["sequence"]) == sequence, "declared receipt sequence has a gap")
            ws, rest = row["ws"], row["rest"]
            kind, at, published = ws["e"], uint(ws["T"]), uint(ws["E"])
            received = nanoseconds(row["received_at"])
            require(at * 10**6 <= published * 10**6 <= received, "inconsistent source clocks")
            require(
                received >= last_received and at >= last_at, "receipt or transaction time regressed"
            )
            require(at != last_at, "same-millisecond financial order unproven")
            previous_published = by_kind.get(kind, 0)
            require(published > previous_published, "same-type publication time did not advance")
            if first_received is None:
                first_received = received
            require(
                received - first_received < 24 * 3600 * 10**9, "single connection exceeds 24 hours"
            )

            symbol = rest["symbol"]
            pair = label(pairs[symbol])
            require(
                pair.endswith("/USDT:USDT") and pair.split("/")[0] + "USDT" == symbol,
                "explicit USD-M symbol mapping required",
            )
            campaign = label(row["campaign_id"])
            if kind == "ORDER_TRADE_UPDATE":
                event = fill(ws, rest, at, pair, campaign, orders)
                before = quantities.get(campaign, 0)
                after = before + dec(event["signed_quantity"])
                require(
                    campaign_pairs.get(campaign, pair) == pair
                    and (campaign not in quantities or before > 0)
                    and active.get(pair, campaign) == campaign
                    and after >= 0,
                    "invalid long inventory or campaign binding",
                )
                campaign_pairs[campaign] = pair
                quantities[campaign] = after
                if after:
                    active[pair] = campaign
                else:
                    active.pop(pair)
            elif kind == "ACCOUNT_UPDATE":
                event = funding(ws, rest, at)
                require(
                    campaign_pairs.get(campaign) == pair, "funding campaign has no matched entry"
                )
            else:
                raise ValueError("unsupported financial receipt type")

            require(event["event_id"] not in seen, "duplicate exchange identity")
            seen.add(event["event_id"])
            events.append(
                {
                    **event,
                    "sequence": sequence,
                    "pair": pair,
                    "campaign_id": campaign,
                    "event_at": clock_text(at),
                    "received_at": row["received_at"],
                    "published_at": clock_text(published),
                }
            )
            by_kind[kind] = published
            last_at, last_received = at, received
        except (AttributeError, KeyError, OverflowError, TypeError, ValueError) as exc:
            # Report the row number without echoing raw private payloads.
            if isinstance(exc, KeyError):
                detail = "required field or frozen symbol absent"
            elif type(exc) is ValueError:
                detail = str(exc)
            else:
                detail = "malformed receipt"
            raise ValueError(f"receipt {sequence}: {detail}") from None
    return {
        "account_id": account_id,
        "connection_id": connection_id,
        "market": "futures",
        "sequence_basis": "unknown",
        "evidence_class": evidence,
        "source_authentication_verified": False,
        "complete_from_inception": False,
        "economic_qualified": False,
        "events": events,
    }
