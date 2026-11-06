"""Whole-account initial-order batches and serial fixed-factor entry scheduling.

All inputs are explicit local evidence. Existing native account/guard/lifecycle
journals remain authoritative; no generated receipts, polling or live transport.
This does not replace the separate cancellation/exit runtime or enable bot_start.
"""
from dataclasses import asdict
from decimal import Decimal as D
import json

from freqtrade.persistence import Trade
from native_after_fill import OfflineProtectionExchange
from native_evidence import CapturedOrder, decimal_text
from native_factor_entries import execute_fixed_entry, fixed_entry_signal
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
        """Apply every initial-order report before a single full-account checkpoint.

Reports cover ALL unresolved bound initial requests, even unchanged open orders.
Partial fills stay protection-pending; only fully reconciled closed fills release
virtual reservations. Cancellation receipts use NativeRiskRuntime instead.
"""
        now=aware(now);self._ready(now)
        if not isinstance(batch_id,str) or not batch_id:
            raise RiskError('Explicit observation batch identity required')
        if (type(bot.exchange) is not OfflineProtectionExchange
                or bot.exchange.trading_mode.value!=self.risk.market):
            raise RiskError('Batch requires explicit matching offline protection rules')
        events=tuple(events);quote_receipts=tuple(quote_receipts)
        payload=primitive(dict(evidence=asdict(evidence),receipt=asdict(receipt),quotes=[asdict(q) for q in quote_receipts],
            reports={k:asdict(v) for k,v in reports.items()},events=events,now=stamp(now),
            complete_sequence=complete_sequence,complete_from_inception=complete_from_inception,
            funding_complete=funding_complete,rules_hash=bot.exchange.rules_hash))
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
        if any(e.get('kind')!='fill' or dec(e.get('signed_quantity','0'))<=0 for e in events):
            raise RiskError('Initial-order batch accepts positive entry fills only')
        expected={rid for rid in self.gate.s['requests']
            if self.gate.lifecycle.s['requests'][rid]['lifecycle']['state']!='TERMINAL_RECONCILED'}
        if previous is None and set(reports)!=expected:
            raise RiskError('Batch must cover every unresolved initial entry request')
        if previous is not None and set(reports)!=set(previous['input']['reports']):
            raise StateError('Stored batch report inventory changed')
        bindings={}
        for rid,envelope in reports.items():
            item=self.gate.s['requests'].get(rid)
            if item is None or item.get('native_binding') is None:
                raise RiskError('Unknown submission must recover a native binding before batching')
            b=item['native_binding'];raw=json.loads(envelope.payload_json);initial=item['ccxt_payload']
            if (envelope.evidence_class!='synthetic' or not envelope.source_id
                    or not 0 <= (now-aware(envelope.received_at)).total_seconds() <=15
                    or any(raw.get(k)!=initial.get(k) for k in ('id','symbol','side','amount','price','clientOrderId'))
                    or raw.get('status') not in ('open','closed')
                    or (raw['status']=='closed' and dec(decimal_text(raw.get('filled')))!=dec(decimal_text(raw.get('amount'))))):
                raise RiskError('Batch needs matching initial open or fully filled native reports')
            envelope.normalize()
            bindings[rid]=b
        if any(e.get('native_trade_id') not in {b['trade_id'] for b in bindings.values()} for e in events):
            raise RiskError('Financial events need a report in the same complete batch')
        batch=previous or dict(signature=signature,input=payload,state='PREPARED',applied={})
        self.gate.s.setdefault('account_batches',{})[batch_id]=batch
        self.gate.s['pending_account_batch']=batch_id
        self.gate._persist()  # Durable entry fence before financial/native mutations.
        for event in events:self.provider.append(event)
        ordered=sorted(reports,key=lambda rid:(self.universe.index(bindings[rid]['pair']),rid))
        for rid in ordered:
            binding=bindings[rid];trade=Trade.session.get(Trade,binding['trade_id'])
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
        checked,audit=self.provider.checkpoint(evidence,quote_receipts,now,complete_sequence=complete_sequence,
            complete_from_inception=complete_from_inception,funding_complete=funding_complete)
        self.wallet.confirm(checked,now)
        for rid in ordered:
            self.gate.reconcile_native(rid,'batch-life-'+digest([batch_id,rid])[:28],reports[rid],checked,now,
                fills=self._fills(bindings[rid]['trade_id']),fills_complete=True,sequence_basis='source_ordered')
        decision=self.risk.evaluate(checked.snapshot,now)
        batch.update(state='COMPLETE',checkpoint_cursor=checked.snapshot.cursor,audit=audit,decision=primitive(asdict(decision)))
        self.gate.s['pending_account_batch']=None
        self.gate._persist()  # Failure poisons gate; no earlier child receipt releases entry permission.
        return dict(evidence=checked,audit=audit,decision=decision,protection=dict(batch['applied']))

    def entry_step(self, strategy, inputs, liquidity, exchange, now):
        """At most one native submission, then require a new whole-account batch.

The full signal set is frozen before sizing the first pair. Pair processing uses
the original universe order; source-map insertion order cannot allocate capital.
"""
        now=aware(now);self._ready(now);self.gate.require_account_batch_clear()
        self.wallet.require_entry_clear(now);self.guard.require_entry_clear()
        if set(inputs)!=set(self.universe):raise RiskError('Entry schedule requires the full frozen input universe')
        key=digest([type(strategy).__name__,stamp(now)])
        signals={}
        for pair in self.universe:
            signal,proof=fixed_entry_signal(strategy,pair,inputs[pair],now,self.universe)
            signals[pair]=dict(signal=None if signal is None else primitive(signal),proof=proof)
        schedule=self.gate.s.get('factor_schedules',{}).get(key)
        if schedule is None:
            schedule=dict(at=stamp(now),strategy=type(strategy).__name__,signals=signals,requests={},skipped=[])
            self.gate.s.setdefault('factor_schedules',{})[key]=schedule;self.gate._persist()
        elif schedule['signals']!=signals:raise StateError('Frozen same-bar signal inputs changed')
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
            result=execute_fixed_entry(self.gate,exchange,strategy,self.wallet._checked_evidence,inputs[pair],liquidity[pair],rid,now)
            return dict(state='ENTRY_STEP',pair=pair,request_id=rid,result=result)
        return dict(state='SIGNALS_EXHAUSTED',schedule_key=key)
