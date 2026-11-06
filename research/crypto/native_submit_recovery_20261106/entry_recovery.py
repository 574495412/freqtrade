"""Recover fixed initial entries from durable decisions and local GET receipts.

Native Trade initialization, stop initialization and order-based recalculation
remain provisional until complete native fees/account/lifecycle reconciliation.
No signal recomputation, fresh reservation or POST is available here.
"""
import copy
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal as D
import json
from types import SimpleNamespace

from exit_recovery import columns
from freqtrade.persistence import Trade, Order
from freqtrade.exchange import timeframe_to_minutes
from freqtrade.freqtradebot import FreqtradeBot
from native_cancellations import LocalCancelExchange
from native_evidence import CapturedOrder, decimal_text
from native_snapshot import EPS
from rd1 import RiskError, StateError, dec, digest, stamp, parse, primitive

POLICY='fixed_initial_entry_query_recovery_v1'


def expected_columns(row, exclude):
    values=columns(row,exclude)
    for attr in row.__mapper__.column_attrs:
        if attr.key not in values or values[attr.key] is not None:
            continue
        default=attr.columns[0].default
        if default is not None and default.is_scalar:
            values[attr.key]=default.arg
    return values


def query_initial_entry(driver, exchange, request_id, bot, now):
    driver._attached(now,bot)
    gate=driver.gate;wallet=driver.wallet
    if type(exchange) is not LocalCancelExchange or exchange.trading_mode.value!=gate.lifecycle.risk.market:
        raise RiskError('Initial recovery requires its explicit local query exchange')
    with driver._scope(now,'observe'),gate._mutex:
        gate.require_account_batch_clear()
        item=gate.s['requests'][request_id];life=gate.lifecycle.s['requests'][request_id]['lifecycle']
        if life['state']!='SUBMISSION_UNKNOWN':
            raise StateError('Initial request already observed; use its ordinary account lifecycle')
        if 'recovery_queries' not in item and 'ccxt_payload' in item:
            item['original_submission_receipt'] = {
                'payload': copy.deepcopy(item['ccxt_payload']),
                'received_at': item['received_at'],
            }
        wallet._checked_evidence=None;wallet.s['state']='INITIAL_ENTRY_RECOVERY_PENDING';wallet._persist()
        attempt=dict(at=stamp(now),state='QUERY_UNKNOWN',client_order_id=item['ticket']['client_order_id'])
        item.setdefault('recovery_queries',[]).append(attempt);gate._persist()
        try:
            raw=exchange.fetch_order(item['ticket']['client_order_id'],item['request']['pair'],
                params={'origClientOrderId':item['ticket']['client_order_id']})
        except Exception:
            attempt['transport']=copy.deepcopy(exchange.records[-1]);gate._persist()
            raise
        captured=copy.deepcopy(exchange.records[-1]);received=parse(captured['received_at'])
        attempt.update(state='RECEIVED',transport=captured);gate._persist()
        # Do not replace the prior accepted reply until this one is validated.
        candidate=dict(item,ccxt_payload=raw,received_at=stamp(received))
        report=gate._validate_response(candidate)
        if report['status'] == 'closed' and dec(report['filled']) != dec(report['amount']):
            raise RiskError('Fully closed initial order needs the full executed quantity')
        timestamp=raw.get('timestamp')
        if (type(timestamp) is not int or not parse(item['claimed_at']).timestamp()*1000<=timestamp<=received.timestamp()*1000
                or not 0<=(now-received).total_seconds()<=15):
            raise RiskError('Initial recovery query chronology is invalid')
        old=item.get('ccxt_payload')
        if old and (raw['id']!=old['id'] or dec(report['filled'])<dec(decimal_text(old['filled']))
                or received<parse(item['received_at'])):
            raise RiskError('Initial recovery query identity or cumulative fills regressed')
        if gate.lifecycle.risk.market=='futures' and captured['raw'].get('reduceOnly') is not False:
            raise RiskError('Initial futures query must explicitly be non-reduce-only')
        item.update(state='RESPONSE_CAPTURED',ccxt_payload=copy.deepcopy(raw),venue_payload=captured['raw'],
            received_at=stamp(received),evidence_class='synthetic')
        attempt['state']='VALIDATED'
        gate._persist()
        return CapturedOrder(json.dumps(raw,sort_keys=True),received,'synthetic-initial-entry-query','synthetic')


def _context(driver,exchange,request_id):
    gate=driver.gate;item=gate.s['requests'][request_id];req=item['request'];pair=req['pair']
    proposal = item['ticket']['proposal']
    expected_request = dict(pair=proposal['pair'], ordertype='limit', side='buy',
        amount=float(proposal['quantity']), rate=float(proposal['price']), leverage=1,
        reduceOnly=False, time_in_force='GTC', initial_order=True)
    if req != expected_request:
        raise RiskError('Original request differs from the frozen entry proposal')
    decision=gate.s.get('factor_decisions',{}).get(request_id)
    if (decision is None or decision['proposal']!=item['ticket']['proposal']
            or digest(exchange.markets[pair])!=decision['sizing']['market_rules_hash']):
        raise RiskError('Original fixed decision and market rules are required for native recovery')
    strategy=driver.startup.strategy
    return dict(policy=POLICY,request=copy.deepcopy(req),claimed_at=item['claimed_at'],
        strategy=type(strategy).__name__,entry_tag=decision['signal']['entry_tag'],
        stake=decision['sizing']['native_stake_float'],stoploss=strategy.stoploss,
        timeframe=timeframe_to_minutes(strategy.timeframe),fee=exchange.get_fee(symbol=pair,taker_or_maker='maker'),
        base=exchange.get_pair_base_currency(pair),trading_mode=exchange.trading_mode,
        amount_precision=exchange.get_precision_amount(pair),price_precision=exchange.get_precision_price(pair),
        precision_mode=exchange.precisionMode,precision_mode_price=exchange.precision_mode_price,
        contract_size=exchange.get_contract_size(pair),startup=driver.startup.binding)


def _new_trade(context,raw):
    req=context['request'];rate=req['rate']
    if raw['status']=='closed' or (raw['status'] in ('expired','rejected') and raw['filled']):
        rate=raw.get('average') or raw.get('price') or rate
    trade=Trade(pair=req['pair'],base_currency=context['base'],stake_currency='USDT',
        stake_amount=context['stake'],amount=0,is_open=True,amount_requested=req['amount'],
        fee_open=context['fee'],fee_close=context['fee'],open_rate=rate,open_rate_requested=req['rate'],
        open_date=parse(context['claimed_at']),exchange='binance',strategy=context['strategy'],
        enter_tag=context['entry_tag'],timeframe=context['timeframe'],leverage=1,is_short=False,
        trading_mode=context['trading_mode'],funding_fees=0,
        **{k:context[k] for k in ('amount_precision','price_precision','precision_mode','precision_mode_price','contract_size')})
    trade.adjust_stop_loss(trade.open_rate,context['stoploss'],initial=True)
    order=Order.parse_from_ccxt_object(raw,req['pair'],'buy',req['amount'],req['rate'])
    _keep_observed_fill_time(order, raw)
    order.ft_order_tag=context['entry_tag'];trade.orders.append(order)
    trade.recalc_trade_from_orders()
    return trade,order


def _keep_observed_fill_time(order, raw):
    # The native parser falls back to wall time for a terminal fill without a
    # reported timestamp. Keep that time unknown until source fills arrive.
    if raw['status'] != 'open' and raw['filled'] and raw.get('lastTradeTimestamp') is None:
        order.order_filled_date = None


def _match_original_commit(trade, order, context, receipt):
    if trade is None or len(trade.orders) != 1 or receipt is None:
        raise RiskError('Existing campaign needs a proven original submission receipt')
    original = receipt['payload']
    expected_trade, expected_order = _new_trade(context, original)
    # The parser records its local application time. The original execution
    # clock, saved before POST, is the application time of the original row.
    expected_order.order_update_date = parse(context['claimed_at'])
    observed_order = expected_columns(order, ('id', 'ft_trade_id'))
    expected_fields = expected_columns(expected_order, ('id', 'ft_trade_id'))
    fill_time_unknown = bool(original['filled'] and original.get('lastTradeTimestamp') is None
                             and original['status'] != 'open')
    if fill_time_unknown:
        # Only this field is exempted from equality. Preserve its original
        # value in the journal and require a source fill before using a date.
        expected_fields['order_filled_date'] = observed_order['order_filled_date']
    if (expected_columns(trade, ('id',)) != expected_columns(expected_trade, ('id',))
            or observed_order != expected_fields):
        raise RiskError('Existing initial native state differs from the original submission receipt')
    return dict(response_hash=digest(original), raw=copy.deepcopy(original),
        received_at=receipt['received_at'], trade_after=expected_columns(trade, ('id',)),
        order_after=observed_order, native_binding=None, original_fill_time_unknown=fill_time_unknown)


def recover_initial_entry(driver,exchange,request_id,bot,now):
    """Recover native records without clearing reservation or account barriers."""
    driver._attached(now,bot)
    gate=driver.gate;wallet=driver.wallet
    if type(exchange) is not LocalCancelExchange or exchange.trading_mode.value!=gate.lifecycle.risk.market:
        raise RiskError('Initial recovery requires its explicit local query exchange')
    with driver._scope(now,'observe'),gate._mutex:
        gate.require_account_batch_clear()
        if Trade.session.new or Trade.session.dirty or Trade.session.deleted:
            raise RiskError('Initial recovery requires committed native state')
        item=gate.s['requests'][request_id]
        if gate.lifecycle.s['requests'][request_id]['lifecycle']['state']!='SUBMISSION_UNKNOWN':
            raise StateError('Initial entry already observed; do not replay its construction')
        if not item.get('recovery_queries') or item['recovery_queries'][-1]['state']!='VALIDATED':
            raise RiskError('A captured initial-entry query is required')
        raw=item.get('ccxt_payload')
        if raw is None:raise RiskError('No accepted source reply to recover')
        report=gate._validate_response(item);received=parse(item['received_at'])
        if not 0<=(now-received).total_seconds()<=15:raise RiskError('Initial recovery reply is stale')
        wallet._checked_evidence=None;wallet.s['state']='INITIAL_ENTRY_RECOVERY_PENDING';wallet._persist()
        rows=Trade.session.query(Order).filter(Order.ft_pair==raw['symbol'],Order.order_id==raw['id']).all()
        zero_terminal = raw['status'] in ('canceled','expired','rejected') and dec(report['filled']) == 0
        if zero_terminal and not rows:
            item['zero_query_pending']=True;gate._persist()
            return dict(state='ZERO_TERMINAL_ACCOUNT_PENDING',native_binding=None)
        context=_context(driver,exchange,request_id);context_hash=digest(primitive(context))
        rec=item.get('initial_recovery')
        if rec and rec['context_hash']!=context_hash:raise RiskError('Initial recovery context changed')
        if len(rows)>1:raise RiskError('Ambiguous native initial order')
        order=rows[0] if rows else None;trade=None if order is None else Trade.session.get(Trade,order.ft_trade_id)
        after_matches=False
        if rec is None:
            if any(t is not trade and t.pair==raw['symbol'] and t.is_open for t in Trade.get_trades_proxy()):
                raise RiskError('Another open campaign already occupies this pair')
            steps = []
            if order is not None:
                steps.append(_match_original_commit(trade, order, context, item.get('original_submission_receipt')))
                after_matches = True
            rec=dict(context_hash=context_hash,context=primitive(context),steps=steps,
                original_fill_time_unknown=bool(steps and steps[0]['original_fill_time_unknown']),
                other_trades={str(t.id):columns(t) for t in Trade.get_trades_proxy() if t is not trade},
                other_orders={str(o.id):columns(o) for o in Trade.session.query(Order).all() if o is not order})
            item['initial_recovery']=rec
        else:
            prior=rec['steps'][-1]
            if rows:
                after_matches=(expected_columns(trade,('id',))==prior['trade_after']
                    and expected_columns(order,('id','ft_trade_id'))==prior['order_after'])
                before_matches=(expected_columns(trade,('id',))==prior.get('trade_before')
                    and expected_columns(order,('id','ft_trade_id'))==prior.get('order_before'))
                if not after_matches and not before_matches:
                    raise RiskError('Recovered initial native state differs from prepared result')
            elif prior.get('native_binding') is not None:
                raise RiskError('A previously committed initial order disappeared')
        if (rec['other_trades']!={str(t.id):columns(t) for t in Trade.get_trades_proxy() if t is not trade}
                or rec['other_orders']!={str(o.id):columns(o) for o in Trade.session.query(Order).all() if o is not order}):
            raise RiskError('Other native account state changed during recovery')
        needs_fill_time_clear = rec.get('original_fill_time_unknown', False) and order is not None and order.order_filled_date is not None
        if zero_terminal:
            item['zero_query_pending'] = True
        if rec['steps'] and rec['steps'][-1]['response_hash']==digest(raw) and rows and after_matches and not needs_fill_time_clear:
            binding=gate.bind_native(request_id)
            rec['steps'][-1]['native_binding']=binding;gate._persist()
            return dict(state='NATIVE_BOUND_ACCOUNT_PENDING',native_binding=binding)
        if trade is not None:
            # A later query can add fills before the initial account observation.
            # Preserve the actual first native stop/template and use native recalc.
            draft=Trade(**{a.key:getattr(trade,a.key) for a in trade.__mapper__.column_attrs if a.key!='id'})
            draft_order=Order(**{a.key:getattr(order,a.key) for a in order.__mapper__.column_attrs if a.key not in ('id','ft_trade_id')})
            draft_order.update_from_ccxt_object(raw)
            _keep_observed_fill_time(draft_order, raw)
            if rec.get('original_fill_time_unknown'):
                draft_order.order_filled_date = None
            draft.orders.append(draft_order)
            draft.recalc_trade_from_orders()
        else:draft,draft_order=_new_trade(context,raw)
        step=dict(response_hash=digest(raw),raw=copy.deepcopy(raw),received_at=stamp(received),
            trade_before=None if trade is None else expected_columns(trade,('id',)),
            order_before=None if order is None else expected_columns(order,('id','ft_trade_id')),
            trade_after=expected_columns(draft,('id',)),order_after=expected_columns(draft_order,('id','ft_trade_id')),
            native_binding=None)
        rec['steps'].append(step);gate._persist()
        try:
            if trade is None:trade,order=draft,draft_order;Trade.session.add(trade)
            else:
                order.update_from_ccxt_object(raw)
                _keep_observed_fill_time(order, raw)
                if rec.get('original_fill_time_unknown'):
                    order.order_filled_date = None
                trade.recalc_trade_from_orders()
            Trade.commit()
        except Exception:
            Trade.session.rollback();raise
        actual_trade = expected_columns(trade, ('id',))
        actual_order = expected_columns(order, ('id', 'ft_trade_id'))
        mismatches = [f'{model}.{field}' for model, actual in (('trade', actual_trade), ('order', actual_order))
                      for field, value in actual.items() if value != step[model+'_after'][field]]
        if mismatches:
            raise RiskError('Native initial construction differs from prepared result: ' + ', '.join(mismatches))
        binding=gate.bind_native(request_id);step['native_binding']=binding;gate._persist()
        return dict(state='NATIVE_BOUND_ACCOUNT_PENDING',native_binding=binding)


def reconcile_zero_initial(driver,request_id,batch_id,evidence,receipt,quotes,bot,now,*,
                           complete_sequence,complete_from_inception,funding_complete):
    """Reconcile a zero terminal and remove any proven empty native campaign."""
    driver._attached(now,bot)
    gate,wallet,provider=driver.gate,driver.wallet,driver.wallet.provider
    with driver._scope(now,'observe'),gate._mutex:
        if not isinstance(batch_id, str) or not batch_id:
            raise RiskError('Explicit zero recovery batch identity required')
        key = 'initial-zero-' + digest([request_id, batch_id])[:28]
        if gate.s.get('pending_account_batch') not in (None, key):
            raise StateError('Another whole-account batch needs reconciliation first')
        item=gate.s['requests'][request_id];raw=item.get('ccxt_payload')
        if (not item.get('zero_query_pending') or raw is None or raw['status'] not in ('canceled','expired','rejected')
                or dec(decimal_text(raw['filled']))!=0
                or not item['recovery_queries'] or item['recovery_queries'][-1]['state']!='VALIDATED'):
            raise RiskError('A validated zero-terminal initial query is required')
        envelope=CapturedOrder(json.dumps(raw,sort_keys=True),parse(item['received_at']),
            'synthetic-initial-entry-query','synthetic')
        gate._validate_response(item)
        if (not 0<=(now-envelope.received_at).total_seconds()<=15
                or any(p.pair==raw['symbol'] for p in evidence.snapshot.positions)
                or complete_sequence!=provider.ledger.last_sequence):
            raise RiskError('Zero initial query conflicts with native/source inventory or financial watermark')
        quotes = tuple(quotes)
        payload=primitive(dict(envelope=asdict(envelope),evidence=asdict(evidence),receipt=asdict(receipt),
            quotes=[asdict(q) for q in quotes],complete_sequence=complete_sequence,
            complete_from_inception=complete_from_inception,funding_complete=funding_complete))
        signature=digest(payload);records=item.setdefault('zero_recovery_accounts',{})
        record=records.get(batch_id)
        if record and record['signature']!=signature:
            raise StateError('Zero-terminal recovery account evidence changed')
        if record is None:
            record=dict(batch_id=batch_id,signature=signature,input=payload,state='PREPARED')
            records[batch_id]=record;gate._persist()
        cleanup = record.get('native_cleanup')
        binding = item.get('native_binding') or (cleanup['binding'] if cleanup else None)
        rows = Trade.session.query(Order).filter(Order.ft_pair==raw['symbol'], Order.order_id==raw['id']).all()
        trade = Trade.session.get(Trade, binding['trade_id']) if binding else None
        if any(t is not trade and t.pair == raw['symbol'] and t.is_open for t in Trade.get_trades_proxy()):
            raise RiskError('Unexpected native inventory for the zero-terminal pair')
        if trade is None and (rows or (binding is not None and cleanup is None)):
            raise RiskError('Unknown native order ownership or missing cleanup intent')
        if trade is not None:
            _validate_empty_trade(item, binding, trade, rows, envelope)
        bot.wallets=wallet.stage(key,receipt,evidence,now)
        checked,audit=provider.checkpoint(evidence,quotes,now,complete_sequence=complete_sequence,
            complete_from_inception=complete_from_inception,funding_complete=funding_complete)
        if trade is not None:
            record['native_cleanup'] = dict(binding=copy.deepcopy(binding), state='PREPARED')
            gate.s['pending_account_batch'] = key
            gate._persist()
            _delete_empty_trade(trade, rows[0], raw, bot)
        if binding is not None:
            if Trade.session.get(Trade, binding['trade_id']) is not None or Trade.session.get(Order, binding['order_pk']) is not None:
                raise RiskError('Native empty-campaign cleanup did not finish')
            record['native_cleanup']['state'] = 'NATIVE_DELETED'
            item['native_binding'] = None
            gate._persist()
        wallet.confirm(checked,now)
        result=gate.reconcile_native(request_id,key,envelope,checked,now,
            fills=(),fills_complete=True,sequence_basis='source_ordered')
        record.update(state='TERMINAL_RECONCILED',audit=audit)
        gate.s['pending_account_batch'] = None
        gate._persist()
        return dict(evidence=checked,audit=audit,lifecycle=result)


def _validate_empty_trade(item, binding, trade, rows, envelope):
    recovery = item.get('initial_recovery')
    if (not recovery or len(rows) != 1 or len(trade.orders) != 1 or rows[0] is not trade.orders[0]
            or rows[0].id != binding['order_pk'] or trade.amount != 0 or rows[0].safe_filled != 0
            or rows[0].ft_is_open or trade.nr_of_successful_entries):
        raise RiskError('Zero terminal cleanup requires the one bound empty native campaign')
    envelope.normalize(rows[0])
    prior = recovery['steps'][-1]
    if (expected_columns(trade, ('id',)) != prior['trade_after']
            or expected_columns(rows[0], ('id', 'ft_trade_id')) != prior['order_after']):
        raise RiskError('Native empty campaign differs from its recovered state')


def _delete_empty_trade(trade, order, raw, bot):
    # Terminal reports take the native cleanup branch without cancel transport.
    proxy = SimpleNamespace(strategy=bot.strategy, wallets=SimpleNamespace(update=lambda: None),
                            _notify_enter_cancel=lambda *args, **kwargs: None)
    try:
        if not FreqtradeBot.handle_cancel_enter(proxy, trade, raw, order, 'source-proven zero terminal'):
            raise RiskError('Native terminal cancellation did not confirm an empty campaign')
    except Exception:
        Trade.rollback()
        raise


def reconcile_recovered_terminal_fill(driver, request_id, batch_id, evidence, receipt, quotes, events, bot, now,
                                      *, complete_sequence, complete_from_inception, funding_complete):
    """Apply fills on an entry the source terminated before its first observation."""
    driver._attached(now, bot)
    gate, wallet, provider = driver.gate, driver.wallet, driver.wallet.provider
    events, quotes = tuple(events), tuple(quotes)
    with driver._scope(now, 'observe'), gate._mutex:
        if not isinstance(batch_id, str) or not batch_id:
            raise RiskError('Explicit terminal recovery batch identity required')
        key = 'initial-terminal-' + digest([request_id, batch_id])[:28]
        if gate.s.get('pending_account_batch') not in (None, key):
            raise StateError('Another whole-account batch needs reconciliation first')
        item = gate.s['requests'][request_id]
        raw, binding = copy.deepcopy(item.get('ccxt_payload')), item.get('native_binding')
        if (not item.get('initial_recovery') or not binding or raw is None
                or raw['status'] not in ('closed', 'canceled', 'expired', 'rejected')
                or not item['recovery_queries'] or item['recovery_queries'][-1]['state'] != 'VALIDATED'):
            raise RiskError('Validated terminal entry query and recovered native binding required')
        received = parse(item['received_at'])
        envelope = CapturedOrder(json.dumps(raw, sort_keys=True), received, 'synthetic-initial-entry-query', 'synthetic')
        report = gate._validate_response(item)
        quantity = dec(report['filled'])
        trade = Trade.session.get(Trade, binding['trade_id'])
        if trade is None or not 0 <= (now-received).total_seconds() <= 15 or quantity <= 0 or not events:
            raise RiskError('Fresh positive terminal fill evidence required')
        envelope.normalize(trade.select_order_by_order_id(raw['id']))
        if any(e['kind'] != 'fill' or e['native_trade_id'] != trade.id or e['order_id'] != raw['id']
               or dec(e['signed_quantity']) <= 0 or dec(e['fee_base']) != 0
               or parse(e['received_at']) > received for e in events):
            raise RiskError('Only matching source entry fills are allowed in terminal recovery')
        cost = sum((dec(e['signed_quantity'])*dec(e['price']) for e in events), D(0))
        fees = sum((dec(e['fee_quote']) for e in events), D(0))
        if (sum((dec(e['signed_quantity']) for e in events), D(0)) != quantity or cost <= 0 or fees < 0
                or abs(cost/quantity-dec(decimal_text(raw['average']))) > EPS
                or abs(cost-dec(decimal_text(raw['cost']))) > EPS):
            raise RiskError('Terminal entry fill quantity, cost or fees differ from the report')
        payload = primitive(dict(envelope=asdict(envelope), evidence=asdict(evidence), receipt=asdict(receipt),
            quotes=[asdict(q) for q in quotes], events=events, complete_sequence=complete_sequence,
            complete_from_inception=complete_from_inception, funding_complete=funding_complete,
            protection_rules_hash=bot.exchange.rules_hash))
        signature = digest(payload)
        batches = item.setdefault('terminal_recovery_accounts', {})
        batch = batches.get(key)
        if batch is not None and batch['signature'] != signature:
            raise StateError('Terminal entry batch evidence changed')
        if batch is None:
            batch = batches[key] = dict(signature=signature, input=payload, state='PREPARED')
        gate.s['pending_account_batch'] = key
        gate._persist()
        for event in events:
            provider.append(event)
        bot.wallets = wallet.stage(key, receipt, evidence, now)
        raw['fee'] = dict(currency='USDT', cost=float(fees), rate=float(fees/cost))
        raw['lastTradeTimestamp'] = int(max(parse(event['event_at']) for event in events).timestamp() * 1000)
        derived = CapturedOrder(json.dumps(raw, sort_keys=True), received, 'synthetic-terminal-entry-fees', 'synthetic')
        gate.after_fill_guard.apply(key, bot, trade, derived, now)
        checked, audit = provider.checkpoint(evidence, quotes, now, complete_sequence=complete_sequence,
            complete_from_inception=complete_from_inception, funding_complete=funding_complete)
        wallet.confirm(checked, now)
        lifecycle = gate.reconcile_native(request_id, key, envelope, checked, now,
            fills=driver.loop.runtime._fills(trade.id), fills_complete=True, sequence_basis='source_ordered')
        batch.update(state='TERMINAL_RECONCILED', audit=audit)
        gate.s['pending_account_batch'] = None
        gate._persist()
        return dict(evidence=checked, audit=audit, lifecycle=lifecycle)
