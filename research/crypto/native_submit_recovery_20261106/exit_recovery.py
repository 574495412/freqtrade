"""Recover a source-observed exit Order, never resubmit or apply financial fills.

Uses the official Order parser/update and the existing captured-response binding.
Trade financial columns and all other orders must remain unchanged. A durable
intent recognizes either side of each native commit after acknowledgement loss.
Only the existing synthetic local transport and isolated native DB are supported.
"""
import copy
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'native_factors_20261106'/'sources'))
from freqtrade.enums import ExitType
from freqtrade.persistence import Trade, Order
from native_exits import LocalExitExchange
from native_after_fill import order_state, trade_state
from native_evidence import CapturedOrder, decimal_text
from rd1 import RiskError, StateError, dec, digest, stamp, parse, primitive

POLICY = 'captured_exit_order_rehydration_v1'
META = ('exit_order_status', 'close_rate_requested', 'exit_reason')


def columns(row, exclude=()):
    result = {}
    for attr in row.__mapper__.column_attrs:
        if attr.key in exclude:
            continue
        value = getattr(row, attr.key)
        if isinstance(value, datetime):
            # Native SQLite datetime columns are UTC, often read without tzinfo.
            value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = str(value)
        result[attr.key] = value
    return result


def _matches(order, raw, ticket, received_at):
    if order is None:
        return False
    try:
        CapturedOrder(json.dumps(raw), received_at, 'synthetic-native-exit-receipt', 'synthetic').normalize(order)
        req = ticket['request']
        return (order.ft_trade_id == ticket['trade_id'] and order.ft_order_tag == ticket['client_order_id']
            and dec(decimal_text(order.ft_amount)) == dec(decimal_text(req['amount']))
            and dec(decimal_text(order.ft_price)) == dec(decimal_text(req['rate']))
            and all(getattr(order, field) == raw.get(field) for field in ('average','cost'))
            and order.ft_is_open == (raw['status']=='open')
            and order.symbol == raw['symbol'] and order.side == raw['side']
            and order.ft_fee_base is None and order.funding_fee is None
            and order.ft_cancel_reason is None and order.stop_price == raw.get('stopPrice')
            and order.order_type == raw.get('type')
            and order.order_date_utc.timestamp()*1000 == raw['timestamp'])
    except (RiskError, TypeError, ValueError):
        return False


def _exit_reason(ticket):
    if ticket['reduction_evaluation'] is not None:
        return 'rd1_reduction' if 'rd1_parent_action' in ticket['action'] else 'F2_daily_scale'
    policy = ticket['strategy_evaluation']
    if policy is None:
        return ticket['client_order_id']
    return (policy['exit_tag'] if ticket['exit_type'] == ExitType.EXIT_SIGNAL.value and policy['exit_tag']
            else ticket['exit_type'])


def recover_exit_binding(controller, action_id, now):
    """Bind a captured authoritative receipt; keep all financial reconciliation pending."""
    with controller._mutex:
        controller._ready(now)
        gate, wallet = controller.gate, controller.wallet
        gate.require_account_batch_clear()
        gate.require_research_driver(now, 'observe')
        wallet._ready(now)
        if Trade.session.new or Trade.session.dirty or Trade.session.deleted:
            raise RiskError('Recovery requires committed native rows')
        ticket = controller.s['actions'][action_id]
        if ticket['state'] not in ('SUBMISSION_UNKNOWN','RESPONSE_CAPTURED','NATIVE_BOUND_ACCOUNT_PENDING','OBSERVED_OPEN'):
            raise StateError('Fill application already started; recover through its existing journal')
        if 'response' not in ticket:
            raise RiskError('Query the original client identity before native recovery')
        envelope = controller.envelope(action_id, now)
        raw = json.loads(envelope.payload_json)
        req = ticket['request']
        if (controller.provider.evidence_class != 'synthetic' or req['side'] != 'sell'
                or req['ordertype'] != 'limit' or req['leverage'] != 1 or req['initial_order']
                or req['reduceOnly'] != (controller.risk.market == 'futures')):
            raise RiskError('Recovery requires the original synthetic 1x exit contract')
        wallet._checked_evidence = None
        wallet.s['state'] = 'EXIT_BINDING_RECOVERY_PENDING'
        wallet._persist()
        trade = Trade.session.get(Trade, ticket['trade_id'])
        if (trade is None or not trade.is_open or trade.pair != req['pair'] or trade.is_short
                or trade.leverage != 1 or dec(decimal_text(trade.amount)) != dec(ticket['quantity_before'])):
            raise RiskError('Native campaign changed before exit recovery')
        rows = Trade.session.query(Order).filter(Order.ft_pair==req['pair'], Order.order_id==raw['id']).all()
        if len(rows)>1 or (rows and rows[0].ft_trade_id != trade.id):
            raise RiskError('Captured order identity already belongs to another native campaign')
        order = rows[0] if rows else None
        other = [o for o in trade.orders if o is not order]
        if any(o.ft_is_open for o in other):
            raise RiskError('Another native open order prevents recovery')
        identity = digest({k:ticket[k] for k in ('trade_id','request','client_order_id','claimed_at',
            'attempt','quantity_before','source_position_id','running_funding_credit')})
        record = ticket.get('binding_recovery')
        if record is None:
            if order is not None:
                # An ordinary native submission may already have committed before
                # its acknowledgment was lost. Never rewrite that existing row.
                if not _matches(order,raw,ticket,envelope.received_at):
                    raise RiskError('Existing native row differs from captured exit')
            else:
                controller.provider._native_events()
            record = dict(policy=POLICY, identity=identity, financial_before=columns(trade,META),
                metadata_before={k:getattr(trade,k) for k in META},
                other_orders={str(o.id):columns(o) for o in other}, steps=[], state='PREPARED')
            if order is not None:
                record['steps'].append(dict(at=stamp(now),response_hash=digest(raw),raw=copy.deepcopy(raw),
                    received_at=stamp(envelope.received_at),native_before=columns(order)))
            ticket['binding_recovery'] = record
        if (record['policy'] != POLICY or record['identity'] != identity
                or record['financial_before'] != columns(trade,META)
                or record['other_orders'] != {str(o.id):columns(o) for o in other}):
            raise RiskError('Native recovery identity or financial state changed')
        expected_meta = dict(exit_order_status='', close_rate_requested=req['rate'], exit_reason=_exit_reason(ticket))
        current_meta = {k:getattr(trade,k) for k in META}
        if current_meta not in (record['metadata_before'], expected_meta):
            raise RiskError('Unrecognized native exit metadata during recovery')
        previous = record['steps'][-1] if record['steps'] else None
        if previous:
            before = previous['native_before']
            before_matches = (order is None and before is None) or (order is not None and columns(order)==before)
            after_matches = (order is not None and columns(order)==previous['native_after']) if 'native_after' in previous else (
                _matches(order, previous['raw'],ticket,parse(previous['received_at'])))
            if not before_matches and not after_matches:
                raise RiskError('Native order differs from both sides of recovery commit')
        elif order is not None:
            raise RiskError('Unexpected native order before recovery intent')
        # A new query can advance cumulative fills while a prior DB acknowledgement
        # was lost. The previous step must match before preparing its successor.
        signature = digest(raw)
        if not previous or previous['response_hash'] != signature:
            record['steps'].append(dict(at=stamp(now), response_hash=signature, raw=copy.deepcopy(raw),
                received_at=stamp(envelope.received_at), native_before=None if order is None else columns(order)))
        record['state'] = 'PREPARED'
        controller._persist()  # before creating/updating any native Order
        try:
            if order is None:
                order = Order.parse_from_ccxt_object(raw,trade.pair,trade.exit_side,req['amount'],req['rate'])
                trade.orders.append(order)
            elif not _matches(order,raw,ticket,envelope.received_at):
                order.update_from_ccxt_object(raw)
            order.ft_order_tag = ticket['client_order_id']
            for key,value in expected_meta.items():
                setattr(trade,key,value)
            Trade.commit()
        except Exception:
            Trade.session.rollback()
            raise
        if (columns(trade,META)!=record['financial_before'] or not _matches(order,raw,ticket,envelope.received_at)
                or {str(o.id):columns(o) for o in trade.orders if o is not order} != record['other_orders']):
            raise RiskError('Native recovery did not preserve campaign financial state')
        # Even a FILLED receipt only rehydrates Order. Existing after-fill/account
        # machinery still requires source fees, cash, funding and complete inventory.
        if raw['status'] in ('canceled','expired','rejected') and dec(decimal_text(raw['filled']))==0:
            # The official parser truthfully creates a terminal Order. Prepare
            # the existing native cancellation journal from that observed state;
            # never fake an OPEN order just to pass the ordinary dispatch path.
            previous_zero = ticket.get('zero_application')
            if previous_zero is not None and previous_zero['state']!='PREPARED':
                raise StateError('Zero-fill native application already started')
            ticket['zero_application'] = dict(signature=digest(asdict(envelope)),
                source=primitive(asdict(envelope)),state='PREPARED',trade_before=trade_state(trade),
                order_before=order_state(order),cancel_reason_before=order.ft_cancel_reason,
                exit_fields_before=primitive(dict(exit_reason=trade.exit_reason,close_rate=trade.close_rate,
                    close_rate_requested=trade.close_rate_requested)),origin=POLICY)
        record['state'] = 'NATIVE_BOUND_ACCOUNT_PENDING'
        record['steps'][-1]['native_after'] = columns(order)
        controller.bind(action_id,now)
        return copy.deepcopy(ticket['native_binding'])


def query_and_recover_exit(driver, exchange, action_id, bot, now):
    """Registered observation entry point; only the local query transport is allowed."""
    driver._attached(now,bot)
    controller = driver.loop.exits
    if type(exchange) is not LocalExitExchange or exchange.controller is not controller:
        raise RiskError('Recovery query requires the registered local exit exchange')
    with driver._scope(now,'observe'):
        exchange.query(action_id,now)
        return recover_exit_binding(controller,action_id,now)
