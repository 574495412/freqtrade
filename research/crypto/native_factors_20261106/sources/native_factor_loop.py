"""One explicit account observation and one priority dispatch in local research.

This composes the existing controllers, never invents venue/source responses,
never runs a polling thread and never enables the strategy's disabled bot_start.
"""
from dataclasses import asdict, replace
from decimal import Decimal as D

from freqtrade.persistence import Trade
from native_factor_runtime import NativeFactorRuntime
from native_cancellations import NativeCancellation
from native_exits import NativeRiskExit
from native_strategy_exits import ExitBarInputs, ExitRate, _frame_evidence, _closed
from native_evidence import decimal_text
from rd1 import RiskError, StateError, aware, dec, digest, primitive, stamp

POLICY='cancel_then_full_risk_then_native_exit_and_reduction_then_entry_v1'


class NativeFactorLoop:
    def __init__(self,runtime,cancellations,exits):
        if (type(runtime) is not NativeFactorRuntime or type(cancellations) is not NativeCancellation
                or type(exits) is not NativeRiskExit or cancellations.gate is not runtime.gate
                or exits.gate is not runtime.gate or exits.wallet is not runtime.wallet):
            raise RiskError('Loop needs one registered account and its existing native controllers')
        self.runtime=runtime;self.cancellations=cancellations;self.exits=exits
        self.gate=runtime.gate;self.wallet=runtime.wallet;self.risk=runtime.risk;self.provider=runtime.provider
        binding=dict(policy=POLICY,cancellation=str(cancellations.store.directory.resolve()),
                     exit=str(exits.store.directory.resolve()))
        previous=self.gate.s.get('factor_loop_binding')
        if previous is not None and previous!=binding:raise StateError('Native loop controller binding changed')
        if previous is None:self.gate.s['factor_loop_binding']=binding;self.gate._persist()

    def observe(self,batch_id,evidence,receipt,quotes,reports,events,bot,now,*,complete_sequence,
                complete_from_inception,funding_complete):
        """Connect both controllers on every batch, including unchanged open reports."""
        self.gate.require_research_driver(now,'observe')
        prior=self.gate.s.get('account_batches',{}).get(batch_id)
        cancel_ids=prior['input']['cancel_requests'] if prior else [rid for rid,item in self.cancellations.s['requests'].items()
            if item['state']!='TERMINAL_RECONCILED']
        exit_ids=list(prior['input']['exit_reports']) if prior else [aid for aid,item in self.exits.s['actions'].items()
            if item['state']!='TERMINAL_RECONCILED']
        return self.runtime.observe_account(batch_id,evidence,receipt,quotes,reports,events,bot,now,
            complete_sequence=complete_sequence,complete_from_inception=complete_from_inception,
            funding_complete=funding_complete,cancel_controller=self.cancellations,cancel_requests=cancel_ids,
            exit_controller=self.exits,exit_actions=exit_ids)

    def _rate(self,pair,evidence,now,rates):
        rate=rates.get(pair)
        if rate is not None:
            if (type(rate) is not ExitRate or rate.pair!=pair or rate.kind!='long_exit_bid'
                    or not rate.source_id or rate.evidence_class!=evidence.evidence_class
                    or not 0 <= (now-aware(rate.received_at)).total_seconds() <=15
                    or not 0 <= (aware(rate.received_at)-aware(rate.at)).total_seconds() <=15 or dec(rate.price)<=0):
                raise RiskError('Dispatch needs a fresh explicit independent exit bid')
            return dec(rate.price),rate
        quote=next((q for q in evidence.snapshot.quotes if q.pair==pair),None)
        if quote is None or quote.kind!='spot_bid' or not 0 <= (now-aware(quote.at)).total_seconds()<=15:
            raise RiskError('Futures mark cannot substitute for an independent execution bid')
        return dec(quote.price),None

    def _attempt(self,cycle,kind,target,function):
        # Child controllers persist their financial intents before transport. The
        # parent also prevents repeating a selected step on this account cursor,
        # including an acknowledgement loss between child and parent completion.
        cycle.update(state='DISPATCH_STARTED',kind=kind,target=target);self.gate._persist()
        try:result=function()
        except Exception as exc:
            cycle.update(error_type=type(exc).__name__);self.gate._persist()
            raise
        if kind=='EXIT' and result is None:
            cycle.update(state='EVALUATING',kind=None,target=None);self.gate._persist()
            return None
        if kind=='ENTRY' and (result['state']!='ENTRY_STEP' or result.get('result',{}).get('state')=='SKIPPED'):
            cycle.update(state='IDLE',result=primitive(result));self.gate._persist()
            return dict(state='IDLE',detail=primitive(result))
        cycle.update(state='AWAIT_ACCOUNT',result=primitive(asdict(result)) if kind=='CANCEL' else primitive(result));self.gate._persist()
        return dict(state='DISPATCHED_'+kind,target=target,result=cycle['result'])

    def dispatch(self,bot,now,*,cancel_exchanges,exit_exchanges,exit_rates,strategy_inputs,reduction_inputs,
                 entry_exchange=None,entry_inputs=None,entry_liquidity=None,allow_entry=True,entry_decision_at=None):
        """Use a fresh checked account; never bypass pending native/source effects.

A new observation is mandatory after a transport step. Callers retain explicit
GET/cancel transport responsibilities and supply each actual captured response.
"""
        now=aware(now);self.runtime._ready(now);self.gate.require_account_batch_clear()
        self.gate.require_research_driver(now,'dispatch')
        self.cancellations._ready();self.exits._ready(now)
        evidence=self.wallet._checked_evidence
        if evidence is None:raise RiskError('Dispatch requires a complete account observation')
        cursor=evidence.snapshot.cursor;key=str(cursor)
        cycles=self.gate.s.setdefault('dispatch_cycles',{});cycle=cycles.get(key)
        if cycle and cycle['state'] in ('DISPATCH_STARTED','AWAIT_ACCOUNT'):
            return dict(state='WAIT_ACCOUNT',cursor=cursor,kind=cycle['kind'],target=cycle['target'])
        self.wallet.require_entry_clear(now)
        if bot.wallets is not self.wallet.wallets or not 0 <= (now-aware(evidence.snapshot.at)).total_seconds() <= 15:
            raise RiskError('Dispatch needs the checked native wallet and a fresh original account receipt')
        checkpoint=self.provider.s['checkpoints'].get(key)
        if checkpoint is None or checkpoint['output']!=primitive(asdict(evidence)) or self.provider.s['last_cursor']!=cursor:
            raise RiskError('Dispatch account is not the latest complete source checkpoint')
        # Unobserved initial submissions cannot be bypassed by another phase.
        for rid,item in self.gate.lifecycle.s['requests'].items():
            life=item.get('lifecycle',{})
            if life.get('state')=='TERMINAL_RECONCILED':continue
            if life.get('state')!='OBSERVED_OPEN' or life.get('snapshot_hash')!=digest(asdict(evidence.snapshot)):
                return dict(state='WAIT_ENTRY_OBSERVATION',request_id=rid)
        for aid,item in self.exits.s['actions'].items():
            if item['state'] not in ('TERMINAL_RECONCILED','OBSERVED_OPEN') or (
                    item['state']=='OBSERVED_OPEN' and item.get('reconciled_cursor')!=cursor):
                return dict(state='WAIT_EXIT_OBSERVATION',action_id=aid)
        for rid,item in self.cancellations.s['requests'].items():
            if item['state']=='TERMINAL_RECONCILED':continue
            previous=self.gate.s.get('account_batches',{})
            observed=any(b['state']=='COMPLETE' and b['checkpoint_cursor']==cursor and rid in b['input']['cancel_requests'] for b in previous.values())
            if not observed:return dict(state='WAIT_CANCEL_OBSERVATION',request_id=rid)
        decision=self.risk.evaluate(evidence.snapshot,now)
        urgent=any(a['kind'] in ('CANCEL_ENTRY','EXIT_POSITION') for a in decision.actions)
        input_proof={} if urgent else {pair:dict(source_id=value.source_id,timing=value.timing,
            main=_frame_evidence(_closed(value.main,5 if type(bot.strategy).__name__=='F1Momentum168Native' else 240,now)),
            hourly={p:_frame_evidence(_closed(frame,60,now)) for p,frame in value.hourly.items()},
            exit_rate=None if value.exit_rate is None else asdict(value.exit_rate)) for pair,value in strategy_inputs.items()}
        reduction_proof=None if urgent or reduction_inputs is None else dict(source_id=reduction_inputs.source_id,timing=reduction_inputs.timing,
            hourly={p:_frame_evidence(_closed(frame,60,now)) for p,frame in reduction_inputs.hourly.items()})
        signature=digest(dict(checkpoint=checkpoint,strategy=type(bot.strategy).__name__,at=stamp(now),policy=POLICY,allow_entry=allow_entry,
            entry_decision_at=None if entry_decision_at is None else stamp(entry_decision_at),
            strategy_inputs=input_proof,reductions=reduction_proof,exit_rates={} if urgent else {p:asdict(r) for p,r in exit_rates.items()}))
        if cycle is None:
            cycle=dict(state='EVALUATING',signature=signature,at=stamp(now),cursor=cursor,checked_positions=[])
            cycles[key]=cycle;self.gate._persist()
        elif cycle['signature']!=signature:raise StateError('Dispatch identity changed within a checked cursor')
        # Pending cancel requests already have exactly one DELETE attempt. Their
        # subsequent replies come through observe(), not a second dispatch.
        actions=decision.actions
        cancel_actions={a['target']:a for a in actions if a['kind']=='CANCEL_ENTRY'}
        order_bindings=evidence.order_bindings
        for pair in self.runtime.universe:
            for source_id,binding in order_bindings.items():
                if binding[0]!=pair or source_id not in cancel_actions:continue
                rid=next((rid for rid,item in self.gate.s['requests'].items()
                    if item['native_binding'] and (item['native_binding']['pair'],item['native_binding']['venue_order_id'])==tuple(binding)),None)
                if rid is None:raise RiskError('Risk cancellation lacks a bound initial request')
                if rid in self.cancellations.s['requests']:continue
                if not cancel_actions[source_id]['claimable']:raise RiskError('Cancellation claim unresolved; reconcile before dispatch')
                if rid not in cancel_exchanges:raise RiskError('Selected cancellation needs its explicit local transport')
                return self._attempt(cycle,'CANCEL',rid,lambda:self.cancellations.dispatch(rid,evidence,cancel_exchanges[rid],now))
        positions=sorted(evidence.snapshot.positions,key=lambda p:self.runtime.universe.index(p.pair))
        full={a['target']:a for a in actions if a['kind']=='EXIT_POSITION'}
        for phase in ('FULL_RISK','ORDINARY'):
            for position in positions:
                if (position.position_id in full)!=(phase=='FULL_RISK'):continue
                tid=evidence.position_bindings[position.position_id];trade=Trade.session.get(Trade,tid)
                if trade is None:raise RiskError('Checked native position disappeared')
                if trade.has_open_orders or trade.amount<=0:continue
                if phase=='ORDINARY' and tid in cycle['checked_positions']:continue
                if phase=='FULL_RISK' and not full[position.position_id]['claimable']:
                    raise RiskError('Risk exit claim unresolved; reconcile before dispatch')
                if position.pair not in exit_exchanges:raise RiskError('Position evaluation needs its explicit local exit adapter')
                rate,receipt=self._rate(position.pair,evidence,now,exit_rates)
                inputs=None;reductions=None
                if phase=='ORDINARY':
                    inputs=strategy_inputs.get(position.pair)
                    if type(inputs) is not ExitBarInputs:raise RiskError('Ordinary exit needs explicit signal input availability')
                    if inputs.exit_rate is not None and inputs.exit_rate!=receipt:
                        raise RiskError('Dispatch and native strategy exit prices differ')
                    inputs=replace(inputs,exit_rate=receipt)
                    # Stop/ROI/signal checks run even with other pending orders;
                    # whole-account allocation requires reconciled inventory.
                    if not evidence.snapshot.orders:
                        reductions=reduction_inputs
                result=self._attempt(cycle,'EXIT',tid,lambda:self.exits.dispatch(trade,evidence,exit_exchanges[position.pair],bot,now,
                    limit=str(rate),strategy_inputs=inputs,reduction_inputs=reductions))
                if result is not None:return result
                if not evidence.snapshot.orders and reduction_inputs is None:
                    raise RiskError('Position allocation input availability is required before new entry')
                cycle['checked_positions'].append(tid);self.gate._persist()
            if phase=='FULL_RISK' and full:return dict(state='WAIT_RISK_EXIT',targets=sorted(full))
        if any(a['kind'] in ('CANCEL_ENTRY','REDUCE_EXPOSURE') for a in actions) or not decision.entry_allowed:
            return dict(state='BLOCKED_RISK',risk_state=decision.state,reasons=decision.reasons)
        if any(item['state']!='TERMINAL_RECONCILED' for item in self.cancellations.s['requests'].values()) or any(
                item['state']!='TERMINAL_RECONCILED' for item in self.exits.s['actions'].values()) or (positions and evidence.snapshot.orders):
            return dict(state='WAIT_ORDER')
        if not allow_entry:return dict(state='IDLE',entry_requested=False)
        if entry_exchange is None or entry_inputs is None or entry_liquidity is None:
            raise RiskError('Entry phase needs explicit fixed factor inputs, liquidity and local transport')
        return self._attempt(cycle,'ENTRY',type(bot.strategy).__name__,lambda:self.runtime.entry_step(
            bot.strategy,entry_inputs,entry_liquidity,entry_exchange,now,decision_at=entry_decision_at))
