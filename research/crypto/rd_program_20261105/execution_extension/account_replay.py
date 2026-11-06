"""Explicit ordered-event account arithmetic, separate from execution/qualification.

Events must be supplied by an owning source adapter. A sequence declaration is an
input contract, not proof that a real exchange supplied it. No pricing or funding
event is manufactured from candles. No strategy, exchange client or DB is used.
"""
from decimal import Decimal as D
from pathlib import Path
import copy
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'data_extension'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'p0/risk'))
from book_replay import nanoseconds
from rd1 import dec,digest,RiskError,Position


class AccountReplay:
    def __init__(self,market,*,sequence_basis,evidence_class):
        if market not in ('spot','futures') or sequence_basis not in ('source_ordered','unknown') or evidence_class not in ('synthetic','captured'):
            raise RiskError('explicit market/order/evidence contract required')
        self.market=market;self.sequence_basis=sequence_basis;self.evidence_class=evidence_class
        self.wallet=D(1000);self.positions={};self.pending_funding={};self.settled_funding={}
        self.events={};self.last_sequence=0;self.last_event_ns=None;self.last_received_ns=None
        self.same_time_kinds=set();self.realized=D(0);self.fees=D(0);self.funding_cash=D(0)

    def apply(self,event):
        """Atomic in-memory event application; rejection leaves prior state intact."""
        candidate=copy.deepcopy(self)
        result=candidate._apply(event)
        self.__dict__.update(candidate.__dict__)
        return result

    def _apply(self,event):
        ident=event['event_id']
        if not isinstance(ident,str) or not ident:raise RiskError('event identity required')
        fingerprint=digest(event)
        if ident in self.events:
            if self.events[ident]!=fingerprint:raise RiskError('conflicting duplicate account event')
            return False
        seq=event['sequence'];at=nanoseconds(event['event_at']);received=nanoseconds(event['received_at'])
        if type(seq)!=int or seq!=self.last_sequence+1:raise RiskError('noncontiguous declared account sequence')
        if received<at or (self.last_event_ns is not None and at<self.last_event_ns) or (self.last_received_ns is not None and received<self.last_received_ns):
            raise RiskError('event time/receipt order inconsistent; do not sort to repair')
        kind=event['kind']
        if at!=self.last_event_ns:self.same_time_kinds=set()
        if self.sequence_basis=='unknown' and self.same_time_kinds and (kind=='fill' or 'fill' in self.same_time_kinds):
            raise RiskError('same-time financial event ordering is unproven')
        if kind=='fill':self._fill(event)
        elif kind in ('funding_accrual','funding_settlement'):self._funding(event)
        else:raise RiskError('unsupported account event; no silent deposits/withdrawals')
        self.events[ident]=fingerprint;self.last_sequence=seq;self.last_event_ns=at;self.last_received_ns=received
        self.same_time_kinds.add(kind)
        return True

    def _fill(self,event):
        pair=event['pair'];q,p,fee=(dec(event[k]) for k in ('signed_quantity','price','fee_quote'))
        if not isinstance(pair,str) or not pair or q==0 or p<=0 or fee<0:raise RiskError('invalid fill/fee')
        old=self.positions.get(pair,{'quantity':D(0),'entry_price':D(0),'campaign_id':event['campaign_id']})
        if old['campaign_id']!=event['campaign_id'] or not event['campaign_id']:raise RiskError('campaign identity mismatch')
        before=old['quantity'];after=before+q
        if self.market=='spot' and after<0:raise RiskError('spot short inventory')
        if before and before*after<0:raise RiskError('split reversal into explicit close and new campaign')
        entry=old['entry_price']
        if not before or before*q>0:
            entry=(abs(before)*entry+abs(q)*p)/abs(after)
        else:
            realized=abs(q)*(p-entry)*(1 if before>0 else -1)
            self.realized+=realized
            if self.market=='futures':self.wallet+=realized
        if self.market=='spot':self.wallet-=q*p
        self.wallet-=fee;self.fees+=fee
        if after:self.positions[pair]={'quantity':after,'entry_price':entry,'campaign_id':event['campaign_id']}
        else:self.positions.pop(pair,None)

    def _funding(self,event):
        if self.market!='futures':raise RiskError('spot funding not applicable')
        fid=event['funding_id'];cost=dec(event['cost_quote'])
        if not isinstance(fid,str) or not fid:raise RiskError('funding identity required')
        if fid in self.settled_funding:raise RiskError('funding already settled under another event identity')
        if event['kind']=='funding_accrual':
            if fid in self.pending_funding:raise RiskError('funding already accrued')
            self.pending_funding[fid]=cost
        else:
            if fid in self.pending_funding and self.pending_funding[fid]!=cost:raise RiskError('accrual/settlement amount mismatch')
            self.pending_funding.pop(fid,None);self.settled_funding[fid]=cost
            self.wallet-=cost;self.funding_cash-=cost

    def value(self,quotes,asof,exit_rate,*,funding_complete):
        """Post-event marked NAV. Quotes and remaining exit reserve are explicit."""
        if type(funding_complete)!=bool or not funding_complete:raise RiskError('funding coverage unverified')
        clock=nanoseconds(asof);rate=dec(exit_rate)
        if rate<0 or rate>=1:raise RiskError('invalid exit cost rate')
        if self.last_received_ns is not None and clock<self.last_received_ns:raise RiskError('valuation precedes known events')
        nav=self.wallet-sum(self.pending_funding.values(),D(0));positions=[]
        for pair,pos in self.positions.items():
            if pair not in quotes:raise RiskError('missing valuation quote')
            quote=quotes[pair];price=dec(quote['price']);received=nanoseconds(quote['received_at']);at=nanoseconds(quote['event_at'])
            expected='spot_bid' if self.market=='spot' else 'futures_mark'
            if quote['kind']!=expected or price<=0 or not at<=received<=clock or clock-received>15*10**9 or clock-at>15*10**9:
                raise RiskError('invalid/stale/noncausal valuation quote')
            quantity=pos['quantity'];reserve=abs(quantity)*price*rate
            nav+=quantity*(price if self.market=='spot' else price-pos['entry_price'])-reserve
            positions.append(Position(pos['campaign_id'],pair,quantity,pos['entry_price'],reserve))
        return {'equity':nav,'wallet':self.wallet,'pending_funding_cost':sum(self.pending_funding.values(),D(0)),
                'settled_funding_cash':self.funding_cash,'fees_paid':self.fees,'realized_price_pnl':self.realized,
                'positions':tuple(positions),'evidence_class':self.evidence_class,
                'source_order_certified_by_this_code':False,'economic_qualified':False}
