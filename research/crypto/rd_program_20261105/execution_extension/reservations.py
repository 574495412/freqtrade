"""Isolated durable entry reservations over frozen RD1; never sends orders.

Outstanding requests remain reserved until a future reconciled lifecycle adapter
proves their disposition. No timeout, restart, or absent order releases capacity.
This first layer intentionally has no release or exchange submission interface.
"""
from dataclasses import asdict, replace
from decimal import Decimal as D
from pathlib import Path
import copy
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'p0/risk'))
from rd1 import RD1, Store, Order, StateError, RiskError, dec, digest, primitive, stamp, parse


class Reservations:
    def __init__(self, directory, account_id, market, *, create=False, cost_scenario='base'):
        self._mutex = threading.RLock()
        self._poisoned = False
        self._closed = False
        directory = Path(directory)
        self.risk = RD1(directory/'risk', account_id, market, create=create, cost_scenario=cost_scenario)
        self.store = None
        try:
            identity = dict(self.risk.s['identity'], layer='entry_reservations_v1')
            self.store = Store(directory/'reservations', identity, create=create)
            if create:
                self.s = {'schema_version':1,'sequence':0,'identity':identity,'requests':{}}
                self._persist()
            else:
                self.s = self.store.load()
                self._validate()
        except Exception:
            self.close()
            raise

    def _validate(self):
        try:
            if not isinstance(self.s['requests'],dict):
                raise StateError('invalid requests')
            for key, item in self.s['requests'].items():
                if not key or item['request_id'] != key or item['state'] != 'RESERVED':
                    raise StateError('invalid reservation identity/state')
                if digest(item['proposal']) != item['proposal_hash']:
                    raise StateError('proposal changed')
                proposal = item['proposal']
                if proposal['pair'] not in self.risk.universe or dec(proposal['quantity']) == 0 or dec(proposal['price']) <= 0:
                    raise StateError('invalid persisted quantity/price/pair')
                parse(item['reserved_at'])
        except (KeyError, TypeError, ValueError) as exc:
            raise StateError('invalid reservation state') from exc

    def _persist(self):
        if self._poisoned or self._closed:
            raise StateError('reservation writer unavailable')
        self.s['sequence'] += 1
        try:
            self.store.save(self.s)
        except Exception as exc:
            self._poisoned = True
            raise StateError('reservation not durably acknowledged; reopen and reconcile') from exc

    def close(self):
        with self._mutex:
            self._closed = True
            if self.store is not None:
                self.store.close()
            self.risk.close()

    def active_requests(self):
        return self.s['requests'].values()

    def reserve(self, request_id, snapshot, proposal, now):
        """Return bookkeeping receipt, NOT an order-submission authorization.

        Exact retry returns the original receipt without allocating again. Changed
        payload under the same ID is rejected. RD1 sees only the real snapshot;
        virtual orders affect capacity checks but never alter its cursor history.
        """
        with self._mutex:
            if self._closed or self._poisoned:
                raise StateError('reservation writer unavailable')
            if not isinstance(request_id,str) or not request_id.strip():
                raise RiskError('request identity required')
            payload = primitive(asdict(proposal))
            proposal_hash = digest(payload)
            existing = self.s['requests'].get(request_id)
            if existing:
                if proposal_hash != existing['proposal_hash']:
                    raise StateError('request ID reused with different proposal')
                return {'reserved':True,'duplicate':True,'receipt':copy.deepcopy(existing),
                        'submission_authorized':False}
            allowed, reasons = self.risk.assess_entry(snapshot, proposal, now)
            if not allowed:
                return {'reserved':False,'reasons':list(reasons),'submission_authorized':False}
            virtual = []
            for request in self.active_requests():
                p = request['proposal']
                if p['pair'] == proposal.pair:
                    return {'reserved':False,'reasons':['outstanding_reservation_same_pair'],
                            'submission_authorized':False}
                virtual.append(Order('reservation:'+request['request_id'],p['pair'],
                                     dec(p['quantity']),dec(p['price']),parse(request['reserved_at']),True))
            augmented = replace(snapshot, orders=snapshot.orders+tuple(virtual))
            try:
                quotes = {q.pair:dec(q.price) for q in snapshot.quotes}
                equity = self.risk.equity(snapshot)
                conservative = replace(proposal,price=max(dec(proposal.price),quotes[proposal.pair]))
                reasons = self.risk.exposure(augmented,equity,conservative)['breaches']
                available = dec(snapshot.balance)
                if self.risk.market == 'futures':
                    available -= sum((dec(p.collateral) for p in snapshot.positions),D(0))
                for order in augmented.orders:
                    if order.increases_risk:
                        available -= abs(dec(order.remaining_quantity))*max(dec(order.limit_price),quotes[order.pair])*(1+self.risk.cost)
                need = abs(dec(proposal.quantity))*conservative.price*(1+self.risk.cost)
                if need > available:
                    reasons.append('insufficient_balance_after_reservations')
            except (KeyError,RiskError) as exc:
                return {'reserved':False,'reasons':['unvalued_reservation:'+str(exc)],
                        'submission_authorized':False}
            if reasons:
                return {'reserved':False,'reasons':list(dict.fromkeys(reasons)),
                        'submission_authorized':False}
            item = {'request_id':request_id,'proposal':payload,'proposal_hash':proposal_hash,
                    'reserved_at':stamp(now),'snapshot_cursor':snapshot.cursor,
                    'snapshot_hash':digest(asdict(snapshot)),'state':'RESERVED'}
            self.s['requests'][request_id] = item
            self._persist()
            return {'reserved':True,'duplicate':False,'receipt':copy.deepcopy(item),
                    'submission_authorized':False}
