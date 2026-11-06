"""Read-only native-order lifecycle bridge. Tickets are for isolated replay only.

CCXT reports and complete RD1 snapshots are explicit evidence inputs. No exchange
client, native bot mutation, or network submission is implemented here.
"""
from dataclasses import asdict
import copy

from reservations import Reservations
from rd1 import Entry, StateError, RiskError, dec, digest, parse, stamp, aware


class Lifecycle(Reservations):
    def __init__(self,*args,**kwargs):
        self._snapshot_digest=None
        super().__init__(*args,**kwargs)

    def _validate(self):
        super()._validate()
        for item in self.s['requests'].values():
            life=item.get('lifecycle')
            if life is None:continue
            if life['state'] not in ('SUBMISSION_UNKNOWN','OBSERVED_OPEN','TERMINAL_RECONCILED'):
                raise StateError('invalid lifecycle state')
            if life['attempts']!=1 or not life['client_order_id']:
                raise StateError('invalid attempt identity')

    def active_requests(self):
        for item in super().active_requests():
            life=item.get('lifecycle',{})
            if life.get('state')=='TERMINAL_RECONCILED':continue
            if life.get('state')=='OBSERVED_OPEN' and life.get('snapshot_hash')==self._snapshot_digest:
                continue  # This exact snapshot represents both fill and remaining order.
            yield item

    def reserve(self,request_id,snapshot,proposal,now):
        with self._mutex:
            self._snapshot_digest=digest(asdict(snapshot))
            try:return super().reserve(request_id,snapshot,proposal,now)
            finally:self._snapshot_digest=None

    def claim_for_replay(self,request_id,snapshot,now):
        """At most one durable attempt per ID, including ambiguous interruptions."""
        with self._mutex:
            if self._closed or self._poisoned:raise StateError('writer unavailable')
            item=self.s['requests'][request_id]
            if item.get('lifecycle') is not None:raise StateError('attempt already consumed; reconcile, never resubmit')
            p=copy.deepcopy(item['proposal'])
            p['liquidity_at']=parse(p['liquidity_at'])
            proposal=Entry(**p)
            allowed,reasons=self.risk.assess_entry(snapshot,proposal,now)
            if not allowed:raise RiskError('fresh entry gate failed: '+str(reasons))
            # Re-check portfolio including all outstanding reservations at new prices.
            # The immutable reservation already exists: capacity never counts it twice.
            from dataclasses import replace
            from rd1 import Order
            virtual=[]
            self._snapshot_digest=digest(asdict(snapshot))
            try:
                for pending in self.active_requests():
                    q=pending['proposal']
                    virtual.append(Order('reservation:'+pending['request_id'],q['pair'],dec(q['quantity']),dec(q['price']),parse(pending['reserved_at']),True))
            finally:self._snapshot_digest=None
            augmented=replace(snapshot,orders=snapshot.orders+tuple(virtual))
            equity=self.risk.equity(snapshot)
            if self.risk.exposure(augmented,equity)['breaches']:raise RiskError('reserved portfolio exceeds limits')
            quotes={q.pair:dec(q.price) for q in snapshot.quotes}
            available=dec(snapshot.balance)
            if self.risk.market=='futures':available-=sum(dec(p.collateral) for p in snapshot.positions)
            try:
                needed=sum(abs(dec(o.remaining_quantity))*max(dec(o.limit_price),quotes[o.pair])*(1+self.risk.cost) for o in augmented.orders if o.increases_risk)
            except KeyError as exc:raise RiskError('missing reservation quote') from exc
            if needed>available:raise RiskError('reserved portfolio exceeds available balance')
            client_id='rd1-'+digest({'account':self.s['identity'],'request':request_id})[:28]
            item['lifecycle']={'state':'SUBMISSION_UNKNOWN','attempts':1,'client_order_id':client_id,
                               'claimed_at':stamp(now),'claim_cursor':snapshot.cursor,'order_id':None,
                               'last_report_at':None,'filled':'0','events':{}}
            self._persist()
            return {'request_id':request_id,'client_order_id':client_id,'proposal':copy.deepcopy(item['proposal']),
                    'replay_only':True,'orders_enabled':False}

    def reconcile(self,request_id,event_id,report,snapshot,now,*,campaign_fills=None):
        """Consume normalized CCXT report paired with a complete account snapshot.

        Required extra adapter field: observed_at is receipt time, never invented
        from order creation timestamp. Original amount is retained even on IOC
        expiry; filled/remaining are cumulative base amounts. All inputs remain
        conditional until an owning source adapter authenticates them.
        """
        with self._mutex:
            if self._closed or self._poisoned:raise StateError('writer unavailable')
            item=self.s['requests'][request_id]
            life=item.get('lifecycle')
            if life is None:raise StateError('no claimed attempt')
            if not isinstance(event_id,str) or not event_id:raise RiskError('event ID required')
            signature=digest({'report':report,'snapshot':asdict(snapshot),'campaign_fills':campaign_fills})
            prior=life['events'].get(event_id)
            if prior:
                if prior!=signature:raise StateError('conflicting duplicate evidence')
                return copy.deepcopy(life)
            if life['state']=='TERMINAL_RECONCILED':raise StateError('terminal evidence already finalized')
            p=item['proposal'];qty=dec(p['quantity']);amount=abs(qty)
            if report.get('clientOrderId')!=life['client_order_id'] or report.get('symbol')!=p['pair'] or report.get('side')!=('buy' if qty>0 else 'sell'):
                raise RiskError('report identity mismatch')
            order_id=report.get('id')
            if not isinstance(order_id,str) or not order_id or life['order_id'] not in (None,order_id):raise RiskError('order identity changed')
            if any(x.get('lifecycle',{}).get('order_id')==order_id for k,x in self.s['requests'].items() if k!=request_id):raise RiskError('order bound to another request')
            status=report.get('status')
            if status not in ('open','closed','canceled','expired','rejected'):raise RiskError('unknown native status')
            filled,remaining=dec(report['filled']),dec(report['remaining'])
            if dec(report['amount'])!=amount or filled<0 or remaining<0 or filled+remaining!=amount or filled<dec(life['filled']):raise RiskError('inconsistent cumulative quantities')
            if dec(report['price'])!=dec(p['price']):raise RiskError('unexpected amended limit')
            at=parse(report['observed_at'])
            if at<parse(life['claimed_at']) or (life['last_report_at'] and at<parse(life['last_report_at'])):raise RiskError('report time regression')
            if snapshot.cursor<=life['claim_cursor'] or aware(snapshot.at)<at or aware(snapshot.orders_at)<at or aware(snapshot.balance_at)<at or at>aware(now):raise RiskError('snapshot does not follow report')
            # Evaluate even a halted account: reconciliation may release unfilled
            # capacity but can never clear RD1 latches or authorize a new entry.
            self.risk.evaluate(snapshot,now)
            if not self.risk.s['last_usable'] or not self.risk.actions_safe(snapshot,now) or self.risk.quality(snapshot,now):raise RiskError('unusable reconciliation snapshot')
            expected=(1 if qty>0 else -1)*filled
            campaign=None
            if campaign_fills is not None:
                from native_evidence import replay_campaign
                campaign=replay_campaign(campaign_fills,order_id,p['pair'],1 if qty>0 else -1)
                if dec(campaign['entry_filled'])!=filled:raise RiskError('campaign entry fills do not match order report')
                if any(parse(f['received_at'])>aware(snapshot.at) for f in campaign_fills):raise RiskError('future received fill in snapshot')
                if any(parse(f['at'])<parse(life['claimed_at']) for f in campaign_fills):raise RiskError('fill predates claimed order attempt')
                expected=dec(campaign['signed_remaining_quantity'])
            actual=sum(dec(pos.quantity) for pos in snapshot.positions if pos.pair==p['pair'])
            if actual!=expected:raise RiskError('filled quantity not represented in account; exits require fuller event ledger')
            matches=[o for o in snapshot.orders if o.order_id==order_id]
            terminal=status!='open'
            if terminal:
                if matches:raise RiskError('terminal order still open in snapshot')
            else:
                if len(matches)!=1:raise RiskError('open order missing from snapshot')
                o=matches[0]
                if o.pair!=p['pair'] or dec(o.remaining_quantity)!=(1 if qty>0 else -1)*remaining or not o.increases_risk or o.reduce_only or dec(o.limit_price)!=dec(p['price']):raise RiskError('remaining order mismatch')
            updated=copy.deepcopy(life)
            updated.update(state='TERMINAL_RECONCILED' if terminal else 'OBSERVED_OPEN',order_id=order_id,
                           last_report_at=stamp(at),filled=str(filled),snapshot_hash=digest(asdict(snapshot)))
            updated['events'][event_id]=signature
            if campaign is not None:
                updated['campaign_fill_reconstruction']=campaign
                updated['campaign_fills']=copy.deepcopy(campaign_fills)
            item['lifecycle']=updated
            self._persist()
            return copy.deepcopy(updated)
