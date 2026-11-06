"""Frozen native ProtectionManager and persistent PairLocks for isolated accounts.

This is not full native bot startup. RD1 marked-equity risk remains independent
of the official closed-trade protection calculations and their wallet basis.
"""
import copy
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace

from freqtrade.freqtradebot import FreqtradeBot
from freqtrade.persistence import Trade,PairLocks
from freqtrade.persistence.models import PairLock
from freqtrade.plugins.protectionmanager import ProtectionManager
from native_after_fill import OfflineProtectionExchange
from native_evidence import decimal_text
from rd1 import RiskError,StateError,Store,primitive,digest,stamp,dec


def locks():
    return primitive([dict(id=x.id,pair=x.pair,side=x.side,active=x.active,reason=x.reason,
        start=stamp(x.lock_time.replace(tzinfo=timezone.utc)),end=stamp(x.lock_end_time_utc)) for x in PairLock.session.query(PairLock).order_by(PairLock.id).all()])


class NativeProtections:
    _active=None
    def __init__(self,directory,gate,wallet,strategy,*,create=False,manager=None):
        if gate.runtime_barrier is not wallet or gate.after_fill_guard is None:
            raise RiskError('Native protections need the registered wallet and after-fill guard')
        if NativeProtections._active is not None:raise StateError('Only one native protection runtime may own PairLocks')
        self.gate=gate;self.wallet=wallet;self.guard=gate.after_fill_guard;self.risk=gate.lifecycle.risk
        self.strategy=strategy;self.engine=Trade.session.get_bind();self._poisoned=False
        if type(strategy).__name__ not in ('F1Momentum168Native','F2Volatility24Native','AverageRD1ControlNative'):
            raise RiskError('Native protection strategy outside frozen scope')
        self.timeframe='5m' if type(strategy).__name__=='F1Momentum168Native' else '4h'
        if strategy.timeframe!=self.timeframe or not PairLocks.use_db:
            raise RiskError('Native protections need matching primary timeframe and persistent PairLocks')
        self.spec=copy.deepcopy(self.risk.r['native_protections'])
        if manager is not None and (type(manager) is not ProtectionManager
                or manager._config != strategy.config
                or manager.name_list!=[x['method'] for x in self.spec]
                or [x._protection_config for x in manager._protection_handlers]!=self.spec):
            raise RiskError('Supplied native protection manager differs from frozen specification')
        self.manager=manager if manager is not None else ProtectionManager(dict(strategy.config,timeframe=self.timeframe),copy.deepcopy(self.spec))
        self.store=Store(Path(directory),dict(self.risk.s['identity'],layer='native_protections_v1'),create=create)
        self.binding=dict(identity=self.store.identity,directory=str(self.store.directory.resolve()),timeframe=self.timeframe,spec_hash=digest(self.spec))
        if create:self.s=dict(schema_version=1,sequence=0,identity=self.store.identity,binding=self.binding,events={});self._persist()
        else:
            self.s=self.store.load()
            if self.s['binding']!=self.binding:raise StateError('Protection runtime specification changed')
        for owner in (self.gate,self.guard):
            if owner.s.get('required_native_protections') not in (None,self.binding):raise StateError('Registered native protection binding changed')
            owner.s['required_native_protections']=copy.deepcopy(self.binding);owner._persist()
            owner.native_protections=self
        self.previous_timeframe=PairLocks.timeframe;PairLocks.timeframe=self.timeframe;NativeProtections._active=self

    def close(self):
        self.store.close()
        if NativeProtections._active is self:
            PairLocks.timeframe=self.previous_timeframe;NativeProtections._active=None

    def _persist(self):
        if self._poisoned or self.store.lock.closed:raise StateError('Native protection journal unavailable')
        self.s['sequence']+=1
        try:self.store.save(self.s)
        except Exception as exc:
            self._poisoned=True;raise StateError('Native protection commit acknowledgement lost; reopen and verify') from exc

    def ready(self,now):
        self.wallet.clock.require(now)
        if self._poisoned or self.store.lock.closed:raise StateError('Native protection journal unavailable')
        if (Trade.session.get_bind() is not self.engine or PairLock.session.get_bind() is not self.engine or not Trade.use_db or not PairLocks.use_db
                or PairLocks.timeframe!=self.timeframe or self.strategy.timeframe!=self.timeframe
                or self.risk.r['native_protections']!=self.spec
                or self.manager.name_list!=[x['method'] for x in self.spec]
                or [x._protection_config for x in self.manager._protection_handlers]!=self.spec):
            raise RiskError('Native protection configuration/database changed')

    def apply_closed(self,event,bot,trade,order,now):
        self.ready(now)
        if (type(bot.exchange) is not OfflineProtectionExchange or bot.wallets is not self.wallet.wallets
                or bot.strategy is not self.strategy or trade.is_open or order.ft_order_side!=trade.exit_side or order.safe_filled<=0):
            raise RiskError('Protection close callback differs from its native account/strategy')
        start=decimal_text(bot.wallets.get_starting_balance())
        history=primitive([dict(id=t.id,pair=t.pair,closed_at=stamp(t.close_date_utc),profit=t.close_profit,
            profit_abs=t.close_profit_abs,exit_reason=t.exit_reason) for t in Trade.get_trades_proxy(is_open=False)])
        payload=primitive(dict(event=event,trade_id=trade.id,order_id=order.order_id,now=stamp(now),starting_balance=start,
            closed_trade=next(row for row in history if row['id']==trade.id),history_count=len(history),history_sha256=digest(history),binding=self.binding))
        signature=digest(payload);previous=self.s['events'].get(event)
        if previous:
            if previous['signature']!=signature:raise StateError('Protection close receipt changed')
            return self.verify_closed(event,trade,order)
        before=locks()
        self.s['events'][event]=dict(state='PREPARED',signature=signature,input=payload,before_locks_sha256=digest(before));self._persist()
        messages=[]
        proxy=SimpleNamespace(strategy=self.strategy,wallets=bot.wallets,exchange=bot.exchange,protections=self.manager,
                              rpc=SimpleNamespace(send_msg=lambda message:messages.append(primitive(message))))
        # Actual native auto-lock, per-pair Cooldown and global handlers; RPC is
        # a local receipt list and never reaches an external message transport.
        FreqtradeBot.handle_protections(proxy,trade.pair,trade.trade_direction)
        after=locks();before_ids={x['id'] for x in before}
        # Store own new/effective locks and hashes of complete snapshots. Repeating
        # every old closed trade and lock in every event would grow quadratically.
        relevant=[x for x in after if x['id'] not in before_ids or (x['active'] and x['pair'] in (trade.pair,'*') and x['end']>stamp(now))]
        self.s['events'][event].update(state='APPLIED',after_locks_sha256=digest(after),locks=relevant,created=[x for x in after if x['id'] not in before_ids],messages=messages)
        self._persist();return self.verify_closed(event,trade,order)

    def verify_closed(self,event,trade,order,*,lock_snapshot=None):
        if self._poisoned or self.store.lock.closed:raise StateError('Native protection journal unavailable')
        item=self.s['events'].get(event)
        if (item is None or item['state']!='APPLIED' or item['input']['trade_id']!=trade.id
                or item['input']['order_id']!=order.order_id or trade.is_open):
            raise RiskError('Native closed-trade protection effects are unverified')
        current_trade=primitive(dict(id=trade.id,pair=trade.pair,closed_at=stamp(trade.close_date_utc),profit=trade.close_profit,
            profit_abs=trade.close_profit_abs,exit_reason=trade.exit_reason))
        if current_trade!=item['input']['closed_trade']:raise RiskError('Closed native protection history changed')
        current={x['id']:x for x in locks()} if lock_snapshot is None else lock_snapshot
        if any(current.get(x['id'])!=x for x in item['locks']):raise RiskError('Committed native protection locks changed or disappeared')
        return copy.deepcopy(item)

    def entry_allowed(self,pair,now):
        self.ready(now)
        by_trade={}
        for event,item in self.s['events'].items():by_trade.setdefault(item['input']['trade_id'],[]).append((event,item))
        current={x['id']:x for x in locks()}
        for trade in Trade.get_trades_proxy(is_open=False):
            matching=by_trade.get(trade.id,[])
            if not matching or any(item['state']!='APPLIED' for _,item in matching):
                raise RiskError('Closed native trade has no complete protection receipt')
            for event,item in matching:
                self.verify_closed(event,trade,trade.select_order_by_order_id(item['input']['order_id']),lock_snapshot=current)
        return not PairLocks.is_pair_locked(pair,now,side='long')

    def require_entry(self,pair,now):
        if not self.entry_allowed(pair,now):raise RiskError('Native pair/global protection lock blocks new entry')
