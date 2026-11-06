"""Closed-campaign funding corrections inside the isolated native account batch."""
from decimal import Decimal as D
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'native_submit_recovery_20261106'))
from exit_recovery import columns
from freqtrade.persistence import Trade
from native_account import event_ns
from native_evidence import decimal_text
from native_factor_runtime import NativeFactorRuntime
from native_snapshot import EPS
from rd1 import RiskError, StateError, dec


PROFIT_FIELDS = {'funding_fees', 'realized_profit', 'close_profit', 'close_profit_abs'}


def _number(value):
    return D(0) if value is None else dec(decimal_text(value))


def _native_state(trade):
    return dict(trade=columns(trade), orders={str(order.id): columns(order) for order in trade.orders})


def _closing_order(runtime, trade, batch):
    events = [e for e in runtime.provider.s['events'] if e['native_trade_id'] == trade.id and e['kind'] == 'fill']
    if (trade.is_open or trade.is_short or trade.leverage != 1 or trade.trading_mode.value != 'futures'
            or trade.close_date is None or any(order.ft_is_open for order in trade.orders)
            or not events or sum((dec(e['signed_quantity']) for e in events), D(0)) != 0
            or dec(events[-1]['signed_quantity']) >= 0):
        raise RiskError('Closed funding needs a complete flat 1x native campaign')
    last_fill = events[-1]
    order = trade.select_order_by_order_id(last_fill['order_id'])
    if order is None or order.ft_order_side != trade.exit_side or order.safe_filled <= 0:
        raise RiskError('Closed funding needs the source-bound terminal exit order')
    for event in batch['input']['events']:
        if event['native_trade_id'] == trade.id and event['kind'] in ('funding_accrual', 'funding_settlement'):
            if (event['campaign_id'] != last_fill['campaign_id']
                    or event_ns(event['event_at']) <= event_ns(last_fill['event_at'])):
                raise RiskError('Closed funding booking must follow the source terminal fill')
    return order


def _verify_transition(before, after, order_id, delta, target):
    for key, value in before['trade'].items():
        if key not in PROFIT_FIELDS and after['trade'][key] != value:
            raise RiskError('Closed funding changed a native field outside profit accounting: ' + key)
    for key, order in before['orders'].items():
        for field, value in order.items():
            if (key, field) != (order_id, 'funding_fee') and after['orders'][key][field] != value:
                raise RiskError('Closed funding changed original order evidence')
    for field in ('close_profit_abs', 'realized_profit'):
        if abs(_number(after['trade'][field]) - _number(before['trade'][field]) - delta) > EPS:
            raise RiskError('Native closed profit adjustment differs from source funding')
    if (abs(_number(after['trade']['funding_fees']) - target) > EPS
            or abs(_number(after['orders'][order_id]['funding_fee'])
                   - _number(before['orders'][order_id]['funding_fee']) - delta) > EPS):
        raise RiskError('Native closed funding allocation differs from the source ledger')


def _apply_closed(runtime, trade, order, record, target):
    before_credit = dec(record['credit_before'])
    delta = target - before_credit
    current = _native_state(trade)
    plan = record.get('closed_funding')
    order_id = str(order.id)
    if plan is not None:
        if plan['credit_target'] != str(target) or plan['allocation_order_pk'] != order.id:
            raise StateError('Closed funding source target changed after prepare')
        if current == plan['after']:
            record['state'] = 'NATIVE_APPLIED'
            runtime.gate._persist()
            return
        if current != plan['before']:
            raise RiskError('Native closed campaign differs from both prepared commit states')
    allocated = sum((_number(item.funding_fee) for item in trade.orders), D(0))
    if (abs(_number(trade.funding_fees) - before_credit) > EPS or abs(allocated - before_credit) > EPS
            or _number(trade.funding_fee_running) != 0):
        raise RiskError('Closed native funding does not reconcile to the prior source ledger')
    try:
        if delta:
            # Native closed P&L derives funding from filled orders. Attribute the
            # correction to the terminal exit in the model; cash timing stays in
            # the source event ledger and the original fill date stays intact.
            order.funding_fee = float(_number(order.funding_fee) + delta)
            trade.recalc_trade_from_orders(is_closing=True)
        after = _native_state(trade)
        _verify_transition(current, after, order_id, delta, target)
        if plan is None:
            record['closed_funding'] = dict(credit_target=str(target), credit_delta=str(delta),
                allocation_order_pk=order.id, before=current, after=after)
            runtime.gate._persist()
        elif after != plan['after']:
            raise StateError('Native closed funding replay differs from the prepared state')
        Trade.commit()
    except Exception:
        Trade.rollback()
        raise
    if _native_state(trade) != after:
        raise StateError('Native committed closed funding state differs from the intent')
    record['state'] = 'NATIVE_APPLIED'
    runtime.gate._persist()


def observe_closed_funding(driver, batch_id, evidence, receipt, quotes, reports, events, bot, now, *,
                           complete_sequence, complete_from_inception, funding_complete):
    """Use the registered observation, with closed-funding accounting in its funding step."""
    driver._attached(now, bot)
    runtime = driver.loop.runtime
    if (type(runtime) is not NativeFactorRuntime or '_apply_funding' in vars(runtime)
            or runtime.risk.market != 'futures' or runtime.provider.evidence_class != 'synthetic'):
        raise RiskError('Closed funding requires the registered isolated futures runtime')
    original = runtime._apply_funding

    def apply_funding(batch):
        runtime.gate.require_research_driver(now, 'observe')
        totals = runtime._funding_totals()
        ordinary = {}
        for key, record in batch.get('funding', {}).items():
            trade = Trade.session.get(Trade, int(key))
            if trade is None or trade.is_open:
                ordinary[key] = record
                continue
            order = _closing_order(runtime, trade, batch)
            _apply_closed(runtime, trade, order, record, totals[int(key)])
        original(dict(batch, funding=ordinary))

    # The native replay clock is single-threaded. Scope the hook to this one
    # observation so the frozen driver still owns all checks and dispatches.
    runtime._apply_funding = apply_funding
    try:
        return driver.observe(batch_id, evidence, receipt, quotes, reports, events, bot, now,
            complete_sequence=complete_sequence, complete_from_inception=complete_from_inception,
            funding_complete=funding_complete)
    finally:
        del runtime._apply_funding
