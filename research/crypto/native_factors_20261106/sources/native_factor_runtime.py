"""Whole-account entry/cancellation/exit/funding batches and fixed entry scheduling.

All inputs are explicit local evidence. Existing native account/guard/lifecycle
journals remain authoritative; no generated receipts, polling or live transport.
Cancellation transport and the full main loop remain explicit; bot_start stays disabled.
"""
from dataclasses import asdict
from decimal import Decimal as D
import json

from freqtrade.persistence import Trade
from native_after_fill import OfflineProtectionExchange
from native_evidence import CapturedOrder, decimal_text
from native_factor_entries import execute_fixed_entry, fixed_entry_signal, registered_entry_signal, entry_boundary
from native_exits import NativeRiskExit
from native_cancellations import NativeCancellation
from native_snapshot import EPS
from rd1 import RiskError, StateError, aware, dec, digest, primitive, stamp


class NativeFactorRuntime:
    def __init__(self, gate, wallet):
        if gate.runtime_barrier is not wallet or gate.after_fill_guard is None:
            raise RiskError('Factor runtime needs registered wallet and native protection')
        self.gate=gate; self.wallet=wallet; self.provider=wallet.provider
        self.guard=gate.after_fill_guard; self.risk=gate.lifecycle.risk
        self.universe=tuple(self.risk.plan['markets'][self.risk.market]['pairs'])

    def _ready(self, now):
        self.wallet.clock.require(now);self.provider._ready()
        if self.gate._poisoned or self.gate.store.lock.closed:
            raise StateError('Factor runtime entry journal unavailable')

    def _fills(self, trade_id):
        campaigns={e['campaign_id'] for e in self.provider.s['events'] if e['native_trade_id']==trade_id}
        if len(campaigns)>1:raise RiskError('Ambiguous native campaign')
        return () if not campaigns else self.provider.campaign_fills(next(iter(campaigns)))

    def observe_entries(self, batch_id, evidence, receipt, quote_receipts, reports, events, bot, now,
                        *, complete_sequence, complete_from_inception, funding_complete):
        return self.observe_account(batch_id,evidence,receipt,quote_receipts,reports,events,bot,now,
            complete_sequence=complete_sequence,complete_from_inception=complete_from_inception,
            funding_complete=funding_complete,entry_only=True)

    def observe_account(self, batch_id, evidence, receipt, quote_receipts, reports, events, bot, now,
                        *, complete_sequence, complete_from_inception, funding_complete,
                        exit_controller=None, exit_actions=(), cancel_controller=None, cancel_requests=(), entry_only=False):
        """Apply all supported reports before one complete account checkpoint.

Reports cover ALL unresolved bound initial requests, even unchanged open orders.
        Initial partials remain pending. Claimed cancellation reports are supplied
        in reports too, with exact captured identity provenance. Funding remains
        source-ordered and distinct from wallet settlement.
"""
        now=aware(now);self._ready(now)
        self.gate.require_research_driver(now,'observe')
        if not isinstance(batch_id,str) or not batch_id:
            raise RiskError('Explicit observation batch identity required')
        if (type(bot.exchange) is not OfflineProtectionExchange
                or bot.exchange.trading_mode.value!=self.risk.market):
            raise RiskError('Batch requires explicit matching offline protection rules')
        events=tuple(events);quote_receipts=tuple(quote_receipts)
        exit_actions=tuple(exit_actions);cancel_requests=tuple(cancel_requests)
        if cancel_controller is not None and (type(cancel_controller) is not NativeCancellation or cancel_controller.gate is not self.gate):
            raise RiskError('Account batch cancellation controller differs from the registered account')
        if (cancel_requests and cancel_controller is None) or len(set(cancel_requests))!=len(cancel_requests):
            raise RiskError('Unique registered cancellation requests required')
        cancel_identity=None if cancel_controller is None else dict(identity=cancel_controller.store.identity,
            directory=str(cancel_controller.store.directory.resolve()))
        if exit_controller is not None and (type(exit_controller) is not NativeRiskExit
                or exit_controller.gate is not self.gate or exit_controller.wallet is not self.wallet):
            raise RiskError('Account batch exit controller differs from the registered account')
        if (exit_actions and exit_controller is None) or len(set(exit_actions))!=len(exit_actions):
            raise RiskError('Unique registered exit actions required')
        exit_reports={} if exit_controller is None else {
            aid:dict(response=exit_controller.s['actions'][aid].get('response'),
                     received_at=exit_controller.s['actions'][aid].get('received_at')) for aid in exit_actions}
        payload=primitive(dict(evidence=asdict(evidence),receipt=asdict(receipt),quotes=[asdict(q) for q in quote_receipts],
            reports={k:asdict(v) for k,v in reports.items()},events=events,now=stamp(now),
            complete_sequence=complete_sequence,complete_from_inception=complete_from_inception,
            funding_complete=funding_complete,rules_hash=bot.exchange.rules_hash,
            exit_reports=exit_reports,exit_controller=None if exit_controller is None else exit_controller.store.identity,
            cancel_requests=cancel_requests,cancel_controller=cancel_identity,entry_only=entry_only))
        signature=digest(payload)
        active=self.gate.s.get('pending_account_batch')
        if active not in (None,batch_id):raise StateError('Resume the pending account batch before another observation')
        previous=self.gate.s.get('account_batches',{}).get(batch_id)
        if previous is not None and previous['signature']!=signature:
            raise StateError('Conflicting observation batch input')
        if previous is not None and previous['state']=='COMPLETE' and self.provider.s['last_cursor']!=evidence.snapshot.cursor:
            raise StateError('Historical completed batch cannot replace a newer checkpoint')
        # A newly observed but invalid/incomplete account must revoke the old
        # wallet permission too. Corrected observations use a fresh batch ID.
        bot.wallets=self.wallet.stage('batch-'+batch_id,receipt,evidence,now)
        required_cancel=self.gate.s.get('required_cancel_controller')
        if required_cancel is not None and required_cancel!=cancel_identity:
            raise RiskError('Registered cancellation controller must accompany account observations')
        if entry_only and (cancel_requests or exit_actions or any(e.get('kind')!='fill' or dec(e.get('signed_quantity','0'))<=0 for e in events)):
            raise RiskError('Initial-order batch accepts positive entry fills only')
        expected={rid for rid in self.gate.s['requests']
            if self.gate.lifecycle.s['requests'][rid]['lifecycle']['state']!='TERMINAL_RECONCILED'}
        if previous is None and set(reports)!=expected:
            raise RiskError('Batch must cover every unresolved initial entry request')
        if previous is not None and set(reports)!=set(previous['input']['reports']):
            raise StateError('Stored batch report inventory changed')
        cancellations={}
        if cancel_controller is not None:
            cancel_controller._ready()
            expected_cancel={rid for rid,item in cancel_controller.s['requests'].items() if item['state']!='TERMINAL_RECONCILED'}
            if previous is None and set(cancel_requests)!=expected_cancel:
                raise RiskError('Batch must cover every unresolved cancellation request')
            for rid in cancel_requests:
                captured=cancel_controller._envelope(rid,now)
                if rid not in reports or asdict(captured)!=asdict(reports[rid]):
                    raise RiskError('Cancellation batch needs the exact controller-derived receipt')
                cancellations[rid]=captured
        bindings={}
        for rid,envelope in reports.items():
            item=self.gate.s['requests'].get(rid)
            if item is None or item.get('native_binding') is None:
                raise RiskError('Unknown submission must recover a native binding before batching')
            b=item['native_binding'];raw=json.loads(envelope.payload_json);initial=item['ccxt_payload']
            if (envelope.evidence_class!='synthetic' or not envelope.source_id
                    or not 0 <= (now-aware(envelope.received_at)).total_seconds() <=15
                    or any(raw.get(k)!=initial.get(k) for k in ('id','symbol','side','amount','price','clientOrderId'))
                    or raw.get('status') not in (('open','closed','canceled','expired','rejected') if rid in cancellations else ('open','closed'))
                    or (raw['status']=='closed' and dec(decimal_text(raw.get('filled')))!=dec(decimal_text(raw.get('amount'))))):
                raise RiskError('Batch needs matching initial open or fully filled native reports')
            envelope.normalize()
            bindings[rid]=b
        exits={}
        if exit_controller is not None:
            exit_controller._ready(now)
            expected_exits={aid for aid,item in exit_controller.s['actions'].items() if item['state']!='TERMINAL_RECONCILED'}
            if previous is None and set(exit_actions)!=expected_exits:
                raise RiskError('Batch must cover every unresolved native exit action')
            for aid in exit_actions:
                item=exit_controller.s['actions'][aid];envelope=exit_controller.envelope(aid,now);raw=json.loads(envelope.payload_json)
                if item['native_binding'] is None:
                    raise RiskError('Recover native exit binding before batching')
                exits[aid]=(item,envelope)
        entry_orders={(b['trade_id'],b['venue_order_id']) for b in bindings.values()}
        exit_orders={(item['trade_id'],item['native_binding']['order_id']) for item,_ in exits.values()}
        exit_started=set()
        for event in events:
            tid=event.get('native_trade_id');kind=event.get('kind')
            if kind=='fill':
                q=dec(event.get('signed_quantity','0'));order=(tid,event.get('order_id'))
                if not ((q>0 and order in entry_orders) or (q<0 and order in exit_orders)):
                    raise RiskError('Financial fill needs its matching entry or exit report in this batch')
                if q<0:exit_started.add(tid)
            elif kind in ('funding_accrual','funding_settlement'):
                if self.risk.market!='futures' or tid in exit_started:
                    raise RiskError('Funding must be futures and precede same-batch exit fills; split source checkpoints')
            else:raise RiskError('Unsupported whole-account financial event')
        batch=previous or dict(signature=signature,input=payload,state='PREPARED',applied={})
        if previous is None:
            funding_trades={e['native_trade_id'] for e in events if e['kind'] in ('funding_accrual','funding_settlement')}
            before=self._funding_totals()
            batch['funding']={str(tid):dict(credit_before=str(before.get(tid,D(0))),state='PREPARED') for tid in funding_trades}
        self.gate.s.setdefault('account_batches',{})[batch_id]=batch
        self.gate.s['pending_account_batch']=batch_id
        self.gate._persist()  # Durable entry fence before financial/native mutations.
        for event in events:self.provider.append(event)
        ordered=sorted(reports,key=lambda rid:(self.universe.index(bindings[rid]['pair']),rid))
        for rid in ordered:
            binding=bindings[rid]
            if rid in cancellations and cancellations[rid].normalize()['status']!='open':
                item=cancel_controller.s['requests'][rid]
                if item['state']=='RECEIPT_PENDING':
                    cancel_controller.apply_terminal(rid,bot,now,fills=self._fills(binding['trade_id']),fills_complete=True)
                elif item['state']=='APPLICATION_PENDING':cancel_controller.recover_applied(rid)
                elif item['state'] not in ('NATIVE_APPLIED_ACCOUNT_PENDING','TERMINAL_RECONCILED'):
                    raise RiskError('Cancellation terminal effects are not recoverable yet')
                batch['applied'][rid]=cancel_controller.s['requests'][rid]['state'];self.gate._persist()
                continue
            trade=Trade.session.get(Trade,binding['trade_id'])
            if trade is None:raise RiskError('Bound native entry disappeared')
            envelope=reports[rid];raw=json.loads(envelope.payload_json)
            fills=self._fills(trade.id);filled=dec(decimal_text(raw['filled']))
            if any(f.order_id!=raw['id'] or f.side!='buy' or dec(f.fee_base)!=0 or f.received_at>envelope.received_at for f in fills):
                raise RiskError('Batch fill source/order/base-fee coverage differs')
            quantity=sum((dec(f.quantity) for f in fills),D(0))
            cost=sum((dec(f.quantity)*dec(f.price) for f in fills),D(0))
            fees=sum((dec(f.fee_quote) for f in fills),D(0))
            if quantity!=filled:raise RiskError('Batch cumulative fill quantity differs from complete ledger')
            if filled:
                if (raw.get('average') is None or raw.get('cost') is None or fees<0
                        or abs(cost/filled-dec(decimal_text(raw['average'])))>EPS
                        or abs(cost-dec(decimal_text(raw['cost'])))>EPS):
                    raise RiskError('Batch report lacks matching fill average/cost/fee evidence')
                raw['fee']=dict(currency='USDT',cost=float(fees),rate=float(fees/cost))
                derived=CapturedOrder(json.dumps(raw),envelope.received_at,'synthetic-batch-source-fees','synthetic')
                event_id='batch-fill-'+digest([batch_id,rid])[:28]
                protection=self.guard.apply(event_id,bot,trade,derived,now,return_pending_partial=True)
                batch['applied'][rid]=protection['state']
            else:
                # No fabricated fee or terminal callback for an unfilled order.
                envelope.normalize(trade.select_order_by_order_id(raw['id']))
                batch['applied'][rid]='UNFILLED_OPEN'
            self.gate._persist()
        self._apply_funding(batch)
        for aid,(item,envelope) in exits.items():
            result=exit_controller.apply_observed(aid,bot,now)
            batch['applied']['exit:'+aid]=result['state']
            self.gate._persist()
        checked,audit=self.provider.checkpoint(evidence,quote_receipts,now,complete_sequence=complete_sequence,
            complete_from_inception=complete_from_inception,funding_complete=funding_complete)
        self.wallet.confirm(checked,now)
        for rid in ordered:
            if rid in cancellations and cancellations[rid].normalize()['status']!='open':
                cancel_controller.reconcile(rid,checked,now,fills=self._fills(bindings[rid]['trade_id']))
                continue
            self.gate.reconcile_native(rid,'batch-life-'+digest([batch_id,rid])[:28],reports[rid],checked,now,
                fills=self._fills(bindings[rid]['trade_id']),fills_complete=True,sequence_basis='source_ordered')
        for aid in exits:exit_controller.reconcile(aid,checked,now)
        decision=self.risk.evaluate(checked.snapshot,now)
        batch.update(state='COMPLETE',checkpoint_cursor=checked.snapshot.cursor,audit=audit,decision=primitive(asdict(decision)))
        self.gate.s['pending_account_batch']=None
        self.gate._persist()  # Failure poisons gate; no earlier child receipt releases entry permission.
        return dict(evidence=checked,audit=audit,decision=decision,protection=dict(batch['applied']))

    def _funding_totals(self):
        unique={}
        for event in self.provider.s['events']:
            if event['kind'] in ('funding_accrual','funding_settlement'):
                unique[event['funding_id']]=(event['native_trade_id'],dec(event['cost_quote']))
        totals={}
        for tid,cost in unique.values():totals[tid]=totals.get(tid,D(0))-cost
        return totals

    def _apply_funding(self,batch):
        """Use native running credit, preserving funding already assigned to orders."""
        totals=self._funding_totals()
        for key,record in batch.get('funding',{}).items():
            tid=int(key);trade=Trade.session.get(Trade,tid)
            if trade is None or trade.trading_mode.value!='futures':
                raise RiskError('Funding batch requires an existing native futures campaign')
            target=totals[tid];allocated=sum((dec(decimal_text(o.funding_fee)) for o in trade.orders if o.funding_fee is not None),D(0))
            actual=D(0) if trade.funding_fees is None else dec(decimal_text(trade.funding_fees))
            running=D(0) if trade.funding_fee_running is None else dec(decimal_text(trade.funding_fee_running))
            allowed=(dec(record['credit_before']),target) if 'credit_target' in record else (dec(record['credit_before']),)
            if (min(abs(actual-value) for value in allowed)>EPS
                    or abs(actual-allocated-running)>EPS):
                raise RiskError('Native funding changed outside acknowledged source transition')
            if not trade.is_open and abs(actual-target)>EPS:
                raise RiskError('New post-close funding cost needs explicit closed-profit reconciliation')
            record.update(credit_target=str(target),allocated_credit=str(allocated),running_credit_target=str(target-allocated))
            self.gate._persist()
            trade.set_funding_fees(float(target-allocated))
            Trade.commit()
            if abs(dec(decimal_text(trade.funding_fees))-target)>EPS:
                raise RiskError('Native funding total differs after source synchronization')
            record['state']='NATIVE_APPLIED';self.gate._persist()

    def register_entry_schedule(self, strategy, inputs, now):
        """Persist all signals at the real boundary before any priority dispatch."""
        now=aware(now);self._ready(now);self.gate.require_account_batch_clear()
        self.gate.require_research_driver(now,'dispatch')
        self.gate.require_research_startup(now)
        if entry_boundary(strategy,now)!=now:
            raise RiskError('New signal schedules require the exact frozen boundary')
        if set(inputs)!=set(self.universe):raise RiskError('Entry schedule requires the full frozen input universe')
        key=digest([type(strategy).__name__,stamp(now)])
        signals={}
        for pair in self.universe:
            signal,proof=fixed_entry_signal(strategy,pair,inputs[pair],now,self.universe)
            signals[pair]=dict(signal=None if signal is None else primitive(signal),proof=proof)
        schedule=self.gate.s.get('factor_schedules',{}).get(key)
        if schedule is None:
            schedule=dict(at=stamp(now),strategy=type(strategy).__name__,signals=signals,
                signals_sha256=digest(signals),requests={},skipped=[])
            self.gate.s.setdefault('factor_schedules',{})[key]=schedule;self.gate._persist()
        elif schedule['signals']!=signals or schedule.get('signals_sha256')!=digest(signals):
            raise StateError('Frozen same-bar signal inputs changed')
        return key,schedule

    def entry_step(self, strategy, inputs, liquidity, exchange, now, *, decision_at=None):
        """At most one native submission, then require a new whole-account batch.

The full signal set is frozen before sizing the first pair. Pair processing uses
the original universe order; source-map insertion order cannot allocate capital.
"""
        now=aware(now);self._ready(now);self.gate.require_account_batch_clear()
        self.gate.require_research_driver(now,'dispatch')
        self.gate.require_research_startup(now)
        self.wallet.require_entry_clear(now);self.guard.require_entry_clear()
        if set(inputs)!=set(self.universe):raise RiskError('Entry schedule requires the full frozen input universe')
        decision_at=now if decision_at is None else aware(decision_at)
        if decision_at==now:
            if entry_boundary(strategy,now)!=now:
                return dict(state='SIGNALS_EXHAUSTED',reason='not_fixed_entry_boundary',schedule_key=None)
            key,schedule=self.register_entry_schedule(strategy,inputs,now)
        else:
            key=digest([type(strategy).__name__,stamp(decision_at)])
            for pair in self.universe:
                registered_entry_signal(self.gate,strategy,pair,inputs[pair],decision_at,now,self.universe)
            schedule=self.gate.s['factor_schedules'][key]
        signals=schedule['signals']
        # Unknown/open-but-unobserved submission cannot be bypassed by selecting
        # a different pair. Every observed-open reservation must share this NAV.
        for rid,item in self.gate.lifecycle.s['requests'].items():
            life=item.get('lifecycle',{})
            if life.get('state')=='TERMINAL_RECONCILED':continue
            if (life.get('state')!='OBSERVED_OPEN'
                    or life.get('snapshot_hash')!=digest(asdict(self.wallet._checked_evidence.snapshot))):
                raise RiskError('Reconcile every prior entry against the current account before advancing the schedule')
        for pair in self.universe:
            if signals[pair]['signal'] is None or pair in schedule['skipped']:continue
            if pair in schedule['requests']:
                rid=schedule['requests'][pair]
                record=self.gate.s.get('factor_decisions',{}).get(rid)
                never_prepared=(record is None and rid not in self.gate.s['requests'] and rid not in self.gate.lifecycle.s['requests'])
                if not never_prepared and (record is None or record['state']=='PREPARED'):
                    raise StateError('Scheduled attempt is uncertain; recover it before advancing')
                if not never_prepared:continue
            if pair not in liquidity or liquidity[pair].pair!=pair:
                raise RiskError('Selected entry needs explicit matching liquidity and rules')
            rid=schedule['requests'].get(pair,'factor-'+digest([key,pair])[:28])
            if pair not in schedule['requests']:
                schedule['requests'][pair]=rid;self.gate._persist()
            result=execute_fixed_entry(self.gate,exchange,strategy,self.wallet._checked_evidence,inputs[pair],liquidity[pair],rid,now,
                decision_at=decision_at)
            return dict(state='ENTRY_STEP',pair=pair,request_id=rid,result=result)
        return dict(state='SIGNALS_EXHAUSTED',schedule_key=key)
