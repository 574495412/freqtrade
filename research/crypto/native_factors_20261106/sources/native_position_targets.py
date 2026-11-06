"""Deterministic RD1 limits and once-daily F2 reduction targets.

This is allocation plumbing fixed before financial runs, not parameter search.
Pair caps are applied first, then proportional cluster and portfolio reductions;
excess position count retains the frozen universe order. No target increases a
holding. F2 scales an unscaled risk budget once, never the already-scaled holding.
"""
from dataclasses import dataclass
from decimal import Decimal as D
import math

import pandas as pd
from native_strategy_exits import TIMING, NAMES, _closed, _frame_evidence
from factor_inputs import effective_daily_scale
from native_evidence import decimal_text
from rd1 import RiskError, aware, dec, primitive, stamp

ALLOCATION = 'pair_then_cluster_then_portfolio_proportional_frozen_count_order_v1'


@dataclass(frozen=True)
class PositionReductionInputs:
    hourly: dict[str, pd.DataFrame]
    source_id: str
    timing: str = TIMING


def build_position_targets(provider, evidence, strategy, inputs, now, daily_targets, *, apply_factor=True):
    """Return raw quantity caps plus prospective daily records; no side effects."""
    if type(inputs) is not PositionReductionInputs or not inputs.source_id or inputs.timing != TIMING:
        raise RiskError('Reduction needs explicit source and frozen timing assumption')
    name = type(strategy).__name__
    if (name not in NAMES or dec(decimal_text(strategy.stoploss)) != dec('-.05')
            or strategy.max_entry_position_adjustment != 0 or strategy.use_custom_stoploss
            or strategy.trailing_stop or strategy.order_types.get('stoploss_on_exchange')
            or strategy.timeframe != ('5m' if name=='F1Momentum168Native' else '4h')
            or strategy.minimal_roi != ({} if name=='F1Momentum168Native' else {0:.5})):
        raise RiskError('Reduction strategy differs from fixed no-DCA contract')
    risk = provider.risk; snap = evidence.snapshot; now = aware(now)
    if provider.exit_rate != risk.cost:
        raise RiskError('Account and RD1 cost scenarios differ')
    if snap.orders:
        raise RiskError('Reconcile pending account orders before allocating reductions')
    if risk.quality(snap,now):
        raise RiskError('Reduction target needs complete usable valuation')
    equity = risk.equity(snap)
    if equity <= 0:
        raise RiskError('Nonpositive equity requires full RD1 exit, not allocation')
    quotes = {q.pair:dec(q.price) for q in snap.quotes}
    positions = {p.pair:p for p in snap.positions}
    if len(positions) != len(snap.positions) or any(dec(p.quantity) <= 0 or dec(p.leverage) != 1 for p in snap.positions):
        raise RiskError('Reduction supports one 1x long native campaign per pair')
    groups, correlation_complete = risk.clusters(snap)
    gross_fraction = min(dec(risk.r['gross_exposure_cap']),dec(risk.r['net_absolute_exposure_cap']))
    if 'hwm_warning' in risk.s['latches']:
        gross_fraction = min(gross_fraction,dec(risk.r['hwm_warning_gross_target']))
    ordered = [p for p in risk.plan['markets'][risk.market]['pairs'] if p in positions]
    current = {p:dec(positions[p].quantity)*quotes[p] for p in ordered}
    caps = {p:min(current[p],equity*dec(risk.r['pair_exposure_cap'])) for p in ordered}
    for p in ordered[risk.r['max_open_positions']:]:
        caps[p] = D(0)
    for group in dict.fromkeys(groups[p] for p in ordered):
        pairs = [p for p in ordered if groups[p] == group]
        total = sum((caps[p] for p in pairs),D(0))
        limit = equity*dec(risk.r['correlation_cluster_cap'])
        if total > limit:
            for p in pairs: caps[p] *= limit/total
    total = sum(caps.values(),D(0))
    if total > equity*gross_fraction:
        for p in caps: caps[p] *= equity*gross_fraction/total
    rd1_caps = dict(caps)
    new_daily = {}; factor_status = {}; day = now.date().isoformat()
    if name == 'F2Volatility24Native' and apply_factor:
        if strategy.buy_range_short.value != 8 or strategy.buy_range_long.value != 21:
            raise RiskError('F2 EMA parameters changed')
        for pair in ordered:
            trade_id = evidence.position_bindings[positions[pair].position_id]
            key = f'{name}:{trade_id}:{day}'
            record = daily_targets.get(key)
            if record is None and (now.hour,now.minute,now.second,now.microsecond) == (0,5,0,0):
                hours = _closed(inputs.hourly.get(pair),60,now)
                scale = effective_daily_scale(hours,now)
                if math.isfinite(scale) and 0 < scale <= 1:
                    other_gross = sum((v for p,v in current.items() if p != pair),D(0))
                    other_cluster = sum((v for p,v in current.items() if p != pair and groups[p] == groups[pair]),D(0))
                    base = max(D(0),min(equity*dec(risk.r['pair_exposure_cap']),
                        equity*dec(risk.r['trade_risk_fraction'])/(dec(risk.r['stop_price_distance_fraction'])+2*risk.cost),
                        equity*gross_fraction-other_gross,
                        equity*dec(risk.r['correlation_cluster_cap'])-other_cluster))
                    target_quantity = base*dec(decimal_text(scale))/quotes[pair]
                    record = primitive(dict(strategy=name,trade_id=trade_id,pair=pair,decision_at=stamp(now),
                        account_cursor=snap.cursor,equity=str(equity),valuation_price=str(quotes[pair]),
                        unscaled_budget=str(base),scale=decimal_text(scale),max_remaining_quantity=str(target_quantity),
                        hourly=_frame_evidence(hours),source_id=inputs.source_id,timing=inputs.timing))
                    new_daily[key] = record
                else:
                    factor_status[pair] = 'missing_or_zero_vol_no_new_factor_target'
            if record is not None:
                if (record['trade_id'] != trade_id or record['pair'] != pair or record['strategy'] != name
                        or not D(0) < dec(record['scale']) <= 1 or dec(record['max_remaining_quantity']) < 0):
                    raise RiskError('Stored F2 daily target identity or quantity differs')
                caps[pair] = min(caps[pair],dec(record['max_remaining_quantity'])*quotes[pair])
                factor_status[pair] = dict(state='fixed_daily_target',key=key,record=record)
            elif pair not in factor_status:
                factor_status[pair] = 'no_0005_checkpoint_no_backfilled_target'
    quantities = {p:caps[p]/quotes[p] for p in ordered}
    proof = primitive(dict(allocation=ALLOCATION,at=stamp(now),account_cursor=snap.cursor,strategy=name,
        equity=str(equity),gross_fraction=str(gross_fraction),correlation_complete=correlation_complete,
        groups={p:groups[p] for p in ordered},current_notionals=current,rd1_target_notionals=rd1_caps,
        target_quantities=quantities,factor_status=factor_status,factor_policy_enabled=apply_factor,
        source_id=inputs.source_id,timing=inputs.timing))
    return quantities, new_daily, proof
