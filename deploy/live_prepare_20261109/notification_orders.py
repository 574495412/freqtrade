"""Read native orders and project confirmed quantity changes into the durable outbox."""

# ruff: noqa: RUF001 -- Chinese output.

import json
from datetime import UTC, datetime

from deploy.live_prepare_20261109.notification_design import (
    LABELS,
    display_pair,
    fill_message,
    submission_message,
    text_message,
)
from deploy.live_prepare_20261109.notification_reasons import order_decision


REJECTION_REASONS = {
    "-1013": "交易规则校验未通过",
    "-1015": "委托频率受限",
    "-2010": "新委托被拒",
    "-2015": "API 权限或 IP 限制",
    "-2019": "保证金不足",
    "-2022": "只减仓委托被拒",
    "-4164": "低于最小名义金额",
    "-4192": "交易所冷静期",
    "-4400": "交易所限制新开仓",
    "insufficient_funds": "下单所需资金不足",
}


def utc_stamp(value):
    if value is None:
        return None
    stamp = datetime.fromisoformat(value)
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)).timestamp()


def read_activity(connection, since):
    # Use both native update and fill times. An old resting order can fill after
    # a relay outage. The overlap covers a transaction committing across a poll.
    floor = (
        "" if since is None else datetime.fromtimestamp(since - 120, UTC).isoformat(sep=" ")[:-6]
    )
    rows = connection.execute(
        """SELECT o.order_id, t.id AS trade_id, t.pair, t.is_short, t.leverage,
                  o.filled, o.average, o.status, o.ft_is_open, o.order_date, o.order_filled_date,
                  o.order_update_date, o.ft_order_tag, o.amount, o.ft_amount, o.price,
                  o.order_type, o.ft_order_side, t.enter_tag, t.exit_reason,
                  o.ft_order_side = CASE WHEN t.is_short THEN 'sell' ELSE 'buy' END AS entry,
                  EXISTS(
                      SELECT 1 FROM orders earlier WHERE earlier.ft_trade_id=t.id
                      AND earlier.ft_order_side=o.ft_order_side AND earlier.filled>0
                      AND earlier.id<o.id
                  ) AS addition,
                  NOT t.is_open AND o.id = (
                      SELECT newest.id FROM orders newest WHERE newest.ft_trade_id=t.id
                      AND newest.ft_order_side IN (
                        CASE WHEN t.is_short THEN 'buy' ELSE 'sell' END, 'stoploss')
                      AND newest.filled>0
                      ORDER BY coalesce(newest.order_filled_date,newest.order_update_date,
                                        newest.order_date) DESC, newest.id DESC LIMIT 1
                  ) AS final_exit,
                  t.close_profit_abs
           FROM orders o JOIN trades t ON t.id=o.ft_trade_id
           WHERE o.ft_order_side IN ('buy','sell','stoploss') AND
                 (o.ft_is_open OR o.order_date>=? OR o.order_update_date>=?
                  OR o.order_filled_date>=? OR ?='')
           ORDER BY o.id""",
        (floor, floor, floor, floor),
    )
    result = []
    for row in rows:
        created = utc_stamp(row["order_date"])
        filled = utc_stamp(row["order_filled_date"])
        updated = utc_stamp(row["order_update_date"]) or filled or created
        if updated is None:
            raise ValueError("Order has no native execution clock")
        final = bool(row["final_exit"])
        action = "加仓" if row["addition"] else "开仓"
        if not row["entry"]:
            action = "平仓" if final else "减仓"
        tag = row["ft_order_tag"]
        if not tag or not tag.strip():
            if action == "开仓":
                tag = row["enter_tag"]
            elif row["ft_order_side"] == "stoploss":
                tag = "stoploss_on_exchange"
            elif final:
                tag = row["exit_reason"]
        order = {
            "pair": row["pair"],
            "order_id": row["order_id"],
            "trade_id": row["trade_id"],
            "is_short": bool(row["is_short"]),
            "leverage": row["leverage"] or 1.0,
            "entry": bool(row["entry"]),
            "protective": row["ft_order_side"] == "stoploss",
            "action": action,
            "is_open": bool(row["ft_is_open"]),
            "status": row["status"],
            "filled": row["filled"] or 0.0,
            "average": row["average"],
            "order_tag": tag,
            "requested_amount": row["amount"] or row["ft_amount"],
            "limit_price": row["price"] if row["order_type"] == "limit" else None,
            "closed_pnl": row["close_profit_abs"] if final else None,
            "created_at": created,
            "updated_at": updated,
            "filled_at": filled,
        }
        result.append(order)
    return result


def publish_orders(outbox, connection, market, orders, now, seed, decisions):
    for order in orders:
        quantity = order["filled"]
        if quantity <= 0 and (not order["is_open"] or order.get("protective")):
            continue
        key = (market, order["pair"], order["order_id"])
        seen = connection.execute(
            """SELECT quantity, final, decision FROM order_progress
               WHERE market=? AND pair=? AND order_id=?""",
            key,
        ).fetchone()
        final = quantity > 0 and order["action"] == "平仓" and not order["is_open"]
        fill = quantity > (seen["quantity"] if seen else 0) or (
            final and (seen is None or not seen["final"])
        )
        decision = seen["decision"] if seen else None
        evidence = json.loads(decision) if decision else order_decision(order, decisions)
        if seen and not fill and (decision or evidence is None):
            continue
        connection.execute(
            """INSERT INTO order_progress(market,pair,order_id,quantity,final,decision)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(market,pair,order_id) DO UPDATE SET
               quantity=max(order_progress.quantity,excluded.quantity),
               final=max(order_progress.final,excluded.final),
               decision=coalesce(order_progress.decision,excluded.decision)""",
            (*key, quantity, final, json.dumps(evidence) if evidence is not None else None),
        )
        if seed:
            continue
        if fill:
            outbox.enqueue(fill_message(market, order, evidence), now=now, connection=connection)
        elif seen is None and order["is_open"]:
            outbox.enqueue(
                submission_message(market, order, evidence), now=now, connection=connection
            )


def publish_order_alerts(outbox, conn, current, prior, orders, now):
    """Alert once per rejected order incident or a persistently pending exit."""
    market = current["market"]
    label = LABELS[market]
    active = {}
    seen = prior.get("order_alerts", [])
    # A later acknowledged order for the same instrument and action clears an
    # earlier rejection, including short-lived exchange cooling-off retries.
    last_accept = {}
    for order in orders:
        side = "entry" if order["entry"] else "exit"
        key = (order["pair"], side)
        if order["created_at"] is not None:
            last_accept[key] = max(last_accept.get(key, 0), order["created_at"])
        if (
            not order["entry"]
            and order["is_open"]
            and not order.get("protective")
            and order["created_at"] is not None
            and now - order["created_at"] >= 300
        ):
            key = f"pending:{order['pair']}:{order['order_id']}"
            active[key] = (
                f"{label}｜退出委托待成交超过 5 分钟｜{display_pair(order['pair'])}"
                f"\n查看挂单与剩余仓位｜交易 #{order['trade_id']}"
            )

    for key, rejection in current.get("rejections", {}).items():
        pair, side, code = rejection["pair"], rejection["side"], rejection["code"]
        accepted_at = last_accept.get((pair, side), 0)
        if accepted_at >= rejection["last"]:
            continue
        if code == "-4192" and side == "entry" and now - rejection["first"] < 60:
            continue
        rejected = code in REJECTION_REASONS
        reason = REJECTION_REASONS.get(code, "查看订单状态，避免重复提交")
        code_text = f"（{code}）" if code and code.startswith("-") else ""
        action = "退出" if side == "exit" else "开仓"
        status = "被拒" if rejected else "提交异常"
        followup = "检查挂单与剩余仓位" if side == "exit" else "检查交易限制或可用资金"
        active[key] = (
            f"{label}｜{action}委托{status}｜{display_pair(pair)}\n{reason}{code_text} · {followup}"
        )

    for key, message in active.items():
        if key not in seen:
            outbox.enqueue(text_message(message), now=now, connection=conn)
    return list(active)
