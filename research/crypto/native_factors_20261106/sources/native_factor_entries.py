"""Fixed-factor entry decisions for the isolated local native request path.

Source-labelled bars/books/rules remain assumptions or synthetic evidence. This
does not enable strategy startup, fetch market data, or certify historical fills.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal as D, ROUND_FLOOR
import math
from types import MethodType, SimpleNamespace

import ccxt
import pandas as pd
from freqtrade.enums import SignalDirection
from freqtrade.freqtradebot import FreqtradeBot
from FixedFactorSignals import F1Momentum168Native, F2Volatility24Native, AverageRD1ControlNative
from factor_inputs import effective_daily_scale
from native_entries import ReplayEntryExchange
from native_evidence import decimal_text
from native_strategy_exits import TIMING, _closed, _frame_evidence
from rd1 import Entry, RiskError, StateError, aware, dec, digest, primitive, stamp


@dataclass(frozen=True)
class EntryInputs:
    main: pd.DataFrame | None
    hourly: dict[str, pd.DataFrame]
    source_id: str
    timing: str = TIMING


@dataclass(frozen=True)
class EntryLiquidity:
    pair: str
    bid: str
    ask: str
    visible_ask_depth_20bps: str
    previous_hour_base_volume: str
    volume_hour_open: datetime
    at: datetime
    received_at: datetime
    source_id: str
    evidence_class: str
    rule_source_id: str
    rules_valid_from: datetime
    rules_valid_until: datetime


def fixed_entry_signal(strategy, pair, inputs, now, universe):
    """Run the actual frozen indicator/trend/get_entry_signal methods."""
    now = aware(now)
    # StrategyResolver reloads the module, so class object identity is not stable.
    expected_class = {c.__name__:c for c in (F1Momentum168Native,F2Volatility24Native,AverageRD1ControlNative)}.get(type(strategy).__name__)
    f1 = type(strategy).__name__ == 'F1Momentum168Native'
    if (expected_class is None
            or tuple(strategy.config['exchange']['pair_whitelist']) != tuple(universe)
            or pair not in universe or strategy.can_short or strategy.stoploss != -.05
            or strategy.max_entry_position_adjustment != 0 or strategy.use_custom_stoploss
            or strategy.trailing_stop or strategy.order_types.get('stoploss_on_exchange')
            or strategy.order_types['entry'] != 'limit' or strategy.order_time_in_force['entry'] != 'GTC'
            or strategy.timeframe != ('5m' if f1 else '4h')
            or strategy.minimal_roi != ({} if f1 else {0:.5})):
        raise RiskError('Entry strategy differs from fixed universe or contract')
    for name in ('populate_indicators','populate_entry_trend','populate_exit_trend','get_entry_signal','custom_entry_price'):
        if getattr(getattr(strategy,name),'__code__',None) != getattr(expected_class,name).__code__:
            raise RiskError('Entry signal or price method differs from fixed source')
    if not f1 and (strategy.buy_range_short.value != 8 or strategy.buy_range_long.value != 21):
        raise RiskError('Official EMA parameters changed')
    if type(inputs) is not EntryInputs or not inputs.source_id or inputs.timing != TIMING:
        raise RiskError('Entry bars need explicit source and frozen timing assumption')
    proof = dict(strategy=type(strategy).__name__,pair=pair,at=stamp(now),source_id=inputs.source_id,timing=inputs.timing)
    # Each closed primary signal is considered once, at its next 5m boundary.
    if (now.minute,now.second,now.microsecond) != (5,0,0) or (now.hour != 0 if f1 else now.hour % 4 != 0):
        return None, dict(proof,reason='not_fixed_entry_boundary')
    minutes = 5 if f1 else 240
    main = _closed(inputs.main,minutes,now)
    hours = {p:_closed(inputs.hourly.get(p),60,now) for p in (universe if f1 else (pair,))}
    proof.update(primary=_frame_evidence(main),hourly={p:_frame_evidence(f) for p,f in hours.items()})
    expected = pd.Timestamp(now).floor(f'{minutes}min')-pd.Timedelta(minutes=minutes)
    if (main.empty or main.date.iloc[-1] != expected
            or (not f1 and (len(main)<21 or not (main.date.diff().iloc[1:]==pd.Timedelta(minutes=minutes)).all()))):
        return None, dict(proof,reason='missing_closed_primary_bars')
    had = 'dp' in strategy.__dict__; old = getattr(strategy,'dp',None)
    strategy.dp = SimpleNamespace(get_pair_dataframe=lambda *,pair,timeframe: hours[pair])
    try:
        frame = strategy.populate_indicators(main.copy(),{'pair':pair})
        frame = strategy.populate_entry_trend(frame,{'pair':pair})
        frame = strategy.populate_exit_trend(frame,{'pair':pair})
        direction, tag = strategy.get_entry_signal(pair,strategy.timeframe,frame)
    finally:
        if had: strategy.dp = old
        else: del strategy.dp
    if direction != SignalDirection.LONG:
        return None, dict(proof,reason='no_fixed_long_signal')
    scale = effective_daily_scale(hours[pair],now) if type(strategy).__name__ == 'F2Volatility24Native' else 1.0
    if not math.isfinite(scale) or not 0 < scale <= 1:
        return None, dict(proof,reason='missing_or_zero_volatility')
    return (tag,dec(decimal_text(scale))), dict(proof,reason='fixed_long_signal',entry_tag=tag,scale=decimal_text(scale))


def size_entry(provider, evidence, exchange, wallet, liquidity, scale, now):
    """Conservative unscaled RD1 budget, then one factor scale and round down."""
    risk = provider.risk; snap = evidence.snapshot; now = aware(now); l = liquidity
    if (type(l) is not EntryLiquidity or not l.source_id or not l.rule_source_id
            or l.evidence_class != evidence.evidence_class or l.pair not in risk.universe
            or not 0 <= (now-aware(l.received_at)).total_seconds() <= 15
            or not 0 <= (aware(l.received_at)-aware(l.at)).total_seconds() <= 15
            or not 0 <= (now-aware(l.at)).total_seconds() <= 15
            or aware(l.volume_hour_open) != now.replace(minute=0,second=0,microsecond=0)-timedelta(hours=1)
            or not aware(l.rules_valid_from) <= now < aware(l.rules_valid_until)
            or not 0 < scale <= 1 or provider.exit_rate != risk.cost):
        raise RiskError('Entry liquidity/rules/scale/cost evidence invalid')
    bid,ask,depth,volume = (dec(x) for x in (l.bid,l.ask,l.visible_ask_depth_20bps,l.previous_hour_base_volume))
    if not 0 < bid <= ask or min(depth,volume) <= 0:
        raise RiskError('Invalid entry bid/ask/depth/volume')
    decision = risk.evaluate(snap,now)
    if not decision.entry_allowed:
        return None,dict(reason='rd1_entry_blocked',decision=primitive(asdict(decision)))
    quotes = {q.pair:dec(q.price) for q in snap.quotes}
    if l.pair not in quotes:
        raise RiskError('Missing synchronized entry valuation')
    if any(p.pair == l.pair for p in snap.positions) or any(o.pair == l.pair and o.increases_risk for o in snap.orders):
        return None,dict(reason='no_dca_or_duplicate_pair')
    equity = dec(decision.equity); exposure = risk.exposure(snap,equity)
    if exposure['position_count'] >= risk.r['max_open_positions']:
        return None,dict(reason='position_count')
    groups,_ = risk.clusters(snap)
    available = dec(snap.balance)
    if risk.market == 'futures': available -= sum((dec(p.collateral) for p in snap.positions),D(0))
    available -= sum((abs(dec(o.remaining_quantity))*max(dec(o.limit_price),quotes[o.pair])*(1+risk.cost)
                      for o in snap.orders if o.increases_risk),D(0))
    price = dec(decimal_text(exchange.price_to_precision(l.pair,float(ask))))
    if price != ask:
        raise RiskError('Explicit entry ask is off native price grid')
    valuation = max(price,quotes[l.pair])
    market = exchange.markets[l.pair]
    if (market.get('active') is not True or market.get('quote') != 'USDT'
            or (risk.market == 'spot' and not market.get('spot'))
            or (risk.market == 'futures' and (not market.get('swap') or not market.get('linear') or market.get('settle') != 'USDT'))):
        raise RiskError('Explicit active USDT market rules required')
    tiers = getattr(exchange,'_leverage_tiers',{})
    if risk.market == 'futures' and not tiers.get(l.pair):
        raise RiskError('Explicit futures leverage/notional rules required')
    if exchange.precisionMode != ccxt.TICK_SIZE:
        raise RiskError('Explicit tick-size rules required')
    step = dec(decimal_text(exchange.get_precision_amount(l.pair)))*dec(decimal_text(exchange.get_contract_size(l.pair)))
    minimum = dec(market['limits']['cost']['min'])
    if min(step,minimum) <= 0: raise RiskError('Missing positive native quantity/notional constraints')
    native_max = exchange.get_max_pair_stake_amount(l.pair,float(price),1)
    limits = dict(trade_risk=equity*dec(risk.r['trade_risk_fraction'])/(dec(risk.r['stop_price_distance_fraction'])+2*risk.cost),
        pair=equity*dec(risk.r['pair_exposure_cap']),
        gross=equity*dec(risk.r['gross_exposure_cap'])-dec(exposure['gross']),
        net=equity*dec(risk.r['net_absolute_exposure_cap'])-dec(exposure['net']),
        cluster=equity*dec(risk.r['correlation_cluster_cap'])-dec(exposure['by_cluster'].get(groups[l.pair],0)),
        cash=available/(1+risk.cost),wallet=dec(decimal_text(wallet.get_available_stake_amount()))/price*valuation,
        volume=volume*dec(risk.r['liquidity']['max_fraction_previous_hour_base_volume'])*valuation,
        depth=depth*dec(risk.r['liquidity']['max_fraction_visible_contra_depth_within_20bps'])*valuation)
    if math.isfinite(native_max): limits['native_max'] = dec(decimal_text(native_max))/price*valuation
    budget = max(D(0),min(limits.values()))
    base_qty = (budget/valuation/step).to_integral_value(rounding=ROUND_FLOOR)*step
    def proposal(q):
        return Entry(l.pair,q,price,D(1),dec(risk.r['stop_price_distance_fraction']),
            (ask-bid)/bid*10000,volume,depth,l.at,step,minimum)
    proof = primitive(dict(limits=limits,unscaled_budget=budget,valuation_price=valuation,entry_price=price,scale=scale,
                           liquidity=asdict(l),market_rules_hash=digest(market),leverage_tiers_hash=digest(tiers)))
    if base_qty <= 0: return None,dict(proof,reason='zero_budget')
    base_ok,reasons = risk.assess_entry(snap,proposal(base_qty),now)
    if not base_ok: return None,dict(proof,reason='unscaled_rd1_rejected',reasons=reasons)
    qty = (base_qty*scale/step).to_integral_value(rounding=ROUND_FLOOR)*step
    p = proposal(qty); stake = qty*price
    native_min = exchange.get_min_pair_stake_amount(l.pair,float(price),-.05,1)
    if qty <= 0 or (native_min is not None and stake < dec(decimal_text(native_min))) or stake < minimum:
        return None,dict(proof,reason='minimum_order_skip_no_enlargement')
    allowed,reasons = risk.assess_entry(snap,p,now)
    if not allowed: return None,dict(proof,reason='scaled_rd1_rejected',reasons=reasons)
    return p,dict(proof,reason='sized',quantity=str(qty),stake=str(stake))


def _native_stake(proposal):
    # Native execute_entry divides binary floats before Exchange precision. Find
    # an adjacent float representation giving the EXACT approved float quantity;
    # never relax the existing gate or permit a rounded-up quantity.
    center = float(proposal.quantity*proposal.price)
    candidates = [center,math.nextafter(center,-math.inf),math.nextafter(center,math.inf)]
    for stake in candidates:
        if stake/float(proposal.price) == float(proposal.quantity):
            return stake
    raise RiskError('Approved quantity cannot be represented by native stake/price arithmetic')


@contextmanager
def _scoped_callbacks(strategy, proposal, tag, now, gate):
    """Authorize only this sized request; native defaults stay disabled outside."""
    saved = {n:(n in strategy.__dict__,getattr(strategy,n)) for n in ('custom_stake_amount','confirm_trade_entry')}
    def check(k):
        gate.runtime_barrier.require_entry_clear(now); gate.after_fill_guard.require_entry_clear()
        return (k['pair']==proposal.pair and k['current_time']==now and k['side']=='long'
                and k['entry_tag']==tag)
    def stake(**k):
        expected = _native_stake(proposal)
        if (not check(k) or k['current_rate']!=float(proposal.price) or k['leverage']!=1
                or k['proposed_stake']!=expected or k['max_stake']<expected
                or (k['min_stake'] is not None and k['min_stake']>expected)):
            return 0.0
        return expected
    def confirm(**k):
        return (check(k) and k['order_type']=='limit' and k['time_in_force']=='GTC'
                and k['rate']==float(proposal.price) and k['amount']==float(proposal.quantity))
    strategy.custom_stake_amount = stake; strategy.confirm_trade_entry = confirm
    try: yield
    finally:
        for n,(had,old) in saved.items():
            if had: setattr(strategy,n,old)
            else: delattr(strategy,n)


def execute_fixed_entry(gate, exchange, strategy, evidence, inputs, liquidity, request_id, now):
    """One explicitly clocked decision; durable gate is final submission authority."""
    now = aware(now); wallet = gate.runtime_barrier
    if type(exchange) is not ReplayEntryExchange or exchange.gate is not gate or wallet is None or gate.after_fill_guard is None:
        raise RiskError('Fixed entry requires matching local exchange, native wallet and protection guard')
    gate.require_account_batch_clear()
    wallet.require_entry_clear(now); gate.after_fill_guard.require_entry_clear()
    if evidence != wallet._checked_evidence:
        raise RiskError('Entry evidence differs from current wallet checkpoint')
    if request_id in gate.s.get('factor_decisions',{}) or request_id in gate.s['requests']:
        raise StateError('Factor entry request already evaluated; reconcile instead of resubmitting')
    signal_key = digest([type(strategy).__name__,liquidity.pair,stamp(now)])
    if signal_key in gate.s.get('factor_signal_requests',{}):
        raise StateError('Fixed signal already evaluated under another request identity')
    risk = gate.lifecycle.risk
    signal,signal_proof = fixed_entry_signal(strategy,liquidity.pair,inputs,now,risk.plan['markets'][risk.market]['pairs'])
    p = None; sizing = None
    if signal is not None:
        tag,scale = signal
        protection_binding=gate.s.get('required_native_protections')
        protections=getattr(gate,'native_protections',None)
        if protection_binding is not None and (protections is None or protections.binding!=protection_binding):
            raise StateError('Registered native protections must be reattached before factor entry')
        if protection_binding is not None and not protections.entry_allowed(liquidity.pair,now):
            sizing=dict(approved=False,reasons=['native_protection_lock'])
        else:p,sizing = size_entry(wallet.provider,evidence,exchange,wallet.wallets,liquidity,scale,now)
        if p is not None: sizing['native_stake_float'] = _native_stake(p)
    record = primitive(dict(signal=signal_proof,sizing=sizing,account_cursor=evidence.snapshot.cursor,
                            state='PREPARED' if p else 'SKIPPED',proposal=None if p is None else asdict(p)))
    gate.s.setdefault('factor_decisions',{})[request_id] = record
    gate.s.setdefault('factor_signal_requests',{})[signal_key] = request_id
    gate._persist()  # No native callbacks or requests before this durable decision.
    if p is None: return record
    # Pure native price/stake/Wallets path. Notifications and terminal fill
    # callbacks require separate explicit source receipts and are deferred.
    proxy = SimpleNamespace(exchange=exchange,strategy=strategy,wallets=wallet.wallets,
        config=dict(strategy.config,stake_currency='USDT',timeframe=strategy.timeframe),trading_mode=exchange.trading_mode,
        _notify_enter=lambda *a,**k: gate.bind_native(request_id),update_trade_state=lambda *a,**k: False)
    for name in ('get_valid_enter_price_and_stake','get_valid_price'):
        setattr(proxy,name,MethodType(getattr(FreqtradeBot,name),proxy))
    with _scoped_callbacks(strategy,p,tag,now,gate), exchange.for_entry(request_id,evidence,p,now):
        submitted = FreqtradeBot.execute_entry(proxy,p.pair,_native_stake(p),price=float(p.price),enter_tag=tag,leverage_=1)
    record['state'] = 'NATIVE_SUBMITTED_RECONCILIATION_PENDING' if submitted else 'NATIVE_DECLINED'
    gate._persist()
    return record
