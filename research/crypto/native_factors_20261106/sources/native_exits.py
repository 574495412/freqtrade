"""RD1 full-position exit intents through actual native/CCXT order builders.

Only explicit synthetic local transport is supported. Entry partials must first
be canceled and reconciled into native inventory; no synthetic terminal status
or shadow Trade is created to make an unrepresented partial position exitable.
"""
import copy
from contextvars import ContextVar
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import threading
from types import MethodType

from freqtrade.enums import TradingMode, MarginMode, ExitType, ExitCheckTuple
from freqtrade.exchange.binance import Binance
from freqtrade.freqtradebot import FreqtradeBot
from freqtrade.persistence import Trade, Order
from native_entries import LocalBinanceTape
from native_after_fill import OfflineProtectionExchange
from native_evidence import CapturedOrder, decimal_text
from native_snapshot import EPS
from rd1 import RiskError, StateError, Store, dec, digest, parse, primitive, stamp, aware


class LocalExitExchange(Binance):
    def __init__(self, transport, controller):
        if type(transport) is not LocalBinanceTape or transport.allowed_methods != {"POST","GET"}:
            raise RiskError("Exit requires the built-in POST/GET-only local tape")
        self.controller = controller; self._api = transport; self._markets = transport.markets
        self._config = {"dry_run":False,"exchange":{"log_responses":False}}
        self._params = {}; self._is_demo_trading = False; self.log_responses = False
        self._ft_has = {"order_props_in_contracts":["amount","filled","remaining","cost"],
                        "exchange_has_overrides":{"fetchOrder":True}}
        self.trading_mode = TradingMode(controller.risk.market)
        self.margin_mode = MarginMode.ISOLATED if controller.risk.market=="futures" else MarginMode.NONE
        self.ticket = ContextVar("native_exit_ticket",default=None)

    def __del__(self):
        pass

    def close(self):
        self._api.session.close()

    def _get_params(self, *args, **kwargs):
        ticket = self.ticket.get()
        if ticket is None:
            raise StateError("No claimed native exit context")
        result = super()._get_params(*args,**kwargs)
        result["clientOrderId"] = ticket["client_order_id"]
        return result

    def _lev_prep(self,pair,leverage,side,accept_fail=False):
        if leverage != 1 or side != "sell":
            raise RiskError("Only local 1x long reduction supported")

    def get_funding_fees(self, *, pair, amount, is_short, open_date):
        ticket = self.ticket.get()
        if (ticket is None or pair != ticket["request"]["pair"] or is_short
                or dec(decimal_text(amount)) != dec(ticket["quantity_before"])
                or open_date != parse(ticket["last_filled_at"])):
            raise RiskError("Native exit funding request differs from captured campaign")
        return float(dec(ticket["running_funding_credit"]))

    def create_order(self, **kwargs):
        ticket = self.ticket.get()
        if ticket is None or kwargs != ticket["request"]:
            raise RiskError("Native exit request differs from the durably claimed reduction")
        result = super().create_order(**kwargs)
        self.controller.capture(ticket["action"]["id"], result, self._api.last_response)
        return result

    def query(self, action_id, now):
        self.controller._ready(now)
        item = self.controller.s["actions"][action_id]
        if item["state"] not in ("SUBMISSION_UNKNOWN","RESPONSE_CAPTURED","NATIVE_BOUND_ACCOUNT_PENDING"):
            raise StateError("Exit query cannot rewrite a committed fill application")
        if "response" not in item:
            # Query by the stable client ID when the venue ID was lost.
            result = super().fetch_order(item["client_order_id"],item["request"]["pair"],
                params={"origClientOrderId":item["client_order_id"]},count=0)
        else:
            result = super().fetch_order(item["response"]["id"],item["request"]["pair"],count=0)
        self.controller.capture(action_id,result,self._api.last_response,now=now)
        return result


class _ForcedExitStrategy:
    def __init__(self, strategy):
        self.strategy = strategy

    def __getattr__(self,key):
        return getattr(self.strategy,key)

    def confirm_trade_exit(self, **kwargs):
        # RD1 forced exits precede strategy veto; no signal parameters change.
        return True


class _ExitBot:
    def __init__(self, bot, exchange, owner, action_id):
        self.bot = bot; self.exchange = exchange; self.owner = owner; self.action_id = action_id
        forced = owner.s["actions"][action_id]["action"]["kind"] == "EXIT_POSITION"
        self.strategy = _ForcedExitStrategy(bot.strategy) if forced else bot.strategy
        self._exit_reason_cache = {}
        self.get_valid_price = MethodType(FreqtradeBot.get_valid_price,self)
        self._safe_exit_amount = MethodType(FreqtradeBot._safe_exit_amount,self)

    def __getattr__(self,key):
        return getattr(self.bot,key)

    def _notify_exit(self,*args,**kwargs):
        # Keep the semantic native trade.exit_reason (e.g. stop_loss) separate
        # from the stable client identity on its Order; no RPC transport.
        kwargs["order"].ft_order_tag = self.owner.s["actions"][self.action_id]["client_order_id"]

    def update_trade_state(self,*args,**kwargs):
        # An immediate fill needs its fresh source wallet/account and complete
        # fee ledger before after-fill application. The native method still
        # commits the returned Order; do not update Trade with missing inputs.
        self.owner.s["actions"][self.action_id]["terminal_callback_deferred"] = True
        self.owner._persist()
        return False


class NativeRiskExit:
    def __init__(self,directory,gate,wallet_bridge,*,create=False):
        if gate.runtime_barrier is not wallet_bridge or gate.after_fill_guard is None:
            raise RiskError("Native exit requires registered wallet/account and protection wiring")
        self.gate = gate; self.wallet = wallet_bridge; self.provider = wallet_bridge.provider
        self.risk = gate.lifecycle.risk; self.engine = Trade.session.get_bind()
        self.store = Store(Path(directory),dict(self.risk.s["identity"],layer="native_risk_exit_v1"),create=create)
        self._poisoned = False; self._mutex = threading.RLock()
        if create:
            self.s = {"schema_version":1,"identity":self.store.identity,"sequence":0,"actions":{}}
            self._persist()
        else:
            self.s = self.store.load()

    def close(self):
        self.store.close()

    def _ready(self,now):
        if self._poisoned or self.store.lock.closed:
            raise StateError("Exit coordinator writer unavailable")
        self.wallet.clock.require(now)
        if not Trade.use_db or Trade.session.get_bind() is not self.engine:
            raise RiskError("Exit native database changed")

    def _persist(self):
        if self._poisoned or self.store.lock.closed:
            raise StateError("Exit coordinator writer unavailable")
        self.s["sequence"] += 1
        try:
            self.store.save(self.s)
        except Exception as exc:
            self._poisoned = True
            raise StateError("Exit evidence commit not acknowledged; reopen and reconcile") from exc

    def capture(self,action_id,response,raw_receipt,*,now=None):
        item = self.s["actions"][action_id]
        raw,received = raw_receipt
        record = dict(venue_payload=copy.deepcopy(raw),response=copy.deepcopy(response),received_at=stamp(received))
        item.setdefault("receipts",[]).append(record)
        item.update(record,state="RESPONSE_CAPTURED")
        self._persist()  # retain even a mismatching response; never release risk here
        self.envelope(action_id,received if now is None else now)

    def envelope(self,action_id,now):
        item = self.s["actions"][action_id]; raw = item["response"]
        envelope = CapturedOrder(json.dumps(raw),parse(item["received_at"]),"synthetic-native-exit-receipt","synthetic")
        report = envelope.normalize(); wanted = item["request"]
        if (report["clientOrderId"] != item["client_order_id"] or report["symbol"] != wanted["pair"]
                or report["side"] != "sell" or dec(report["amount"]) != dec(decimal_text(wanted["amount"]))
                or dec(report["price"]) != dec(decimal_text(wanted["rate"]))
                or (self.risk.market=="futures" and raw.get("reduceOnly") is not True)):
            raise RiskError("Exit receipt identity, amount, price or reduceOnly differs")
        last = D(0); venue_id = None; last_at = parse(item["claimed_at"])
        for r in item["receipts"]:
            filled = dec(decimal_text(r["response"].get("filled")))
            if filled < last or (venue_id is not None and r["response"].get("id") != venue_id) or parse(r["received_at"]) < last_at:
                raise RiskError("Exit cumulative fills, venue identity or receipt time regressed")
            last = filled; venue_id = r["response"].get("id"); last_at = parse(r["received_at"])
        filled,remaining = dec(report["filled"]),dec(report["remaining"])
        if type(raw.get("timestamp")) is not int:
            raise RiskError("Exit receipt needs original order creation time")
        created = datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(milliseconds=raw["timestamp"])
        if (filled < 0 or remaining < 0 or filled+remaining != dec(report["amount"])
                or report["status"] not in ("open","closed","canceled","expired","rejected")
                or (report["status"]=="closed" and remaining != 0)
                or not parse(item["claimed_at"]) <= created <= envelope.received_at
                or not 0 <= (aware(now)-envelope.received_at).total_seconds() <= 15):
            raise RiskError("Exit receipt status, quantities or freshness invalid")
        return envelope

    def dispatch(self,trade,evidence,exchange,bot,now,*,limit,strategy_inputs=None,reduction_inputs=None):
        with self._mutex:
            self._ready(now); self.gate.require_account_batch_clear(); self.wallet.require_entry_clear(now)
            if type(exchange) is not LocalExitExchange or exchange.controller is not self or bot.wallets is not self.wallet.wallets:
                raise RiskError("Exit bot/exchange/wallet binding differs")
            if (not trade.is_open or trade.amount <= 0 or trade.is_short or trade.leverage != 1
                    or trade.has_open_orders or trade.open_sl_orders or Trade.session.get(Trade,trade.id) is not trade):
                raise RiskError("Exit requires reconciled native position with no pending orders")
            key = str(evidence.snapshot.cursor); checkpoint = self.provider.s["checkpoints"].get(key)
            if (checkpoint is None or checkpoint["output"] != primitive(asdict(evidence))
                    or self.provider.s["last_cursor"] != evidence.snapshot.cursor):
                raise RiskError("Exit needs the checked complete account checkpoint")
            if any(a["trade_id"] == trade.id and a["state"] != "TERMINAL_RECONCILED" for a in self.s["actions"].values()):
                raise StateError("Exit action already attempted; query/reconcile before any retry")
            source_id = next((pid for pid,tid in evidence.position_bindings.items() if tid==trade.id),None)
            decision = self.risk.evaluate(evidence.snapshot,now)
            action = next((a for a in decision.actions if a["kind"]=="EXIT_POSITION" and a["target"]==source_id),None)
            exit_check = ExitCheckTuple(ExitType.FORCE_EXIT); policy = None; allocation = None
            target_quantity = D(0); risk_reduction = None
            if action is None:
                if strategy_inputs is None and reduction_inputs is None:
                    raise RiskError("Frozen RD1 has no full-position exit action")
                exits = []
                if strategy_inputs is not None:
                    from native_strategy_exits import evaluate_fixed_exit
                    try:
                        exits, policy = evaluate_fixed_exit(bot.strategy,trade,evidence,strategy_inputs,now)
                    except Exception:
                        Trade.session.rollback()
                        raise
                    key = digest({"trade_id":trade.id,"strategy":policy["strategy"],"cursor":evidence.snapshot.cursor})
                    observations = self.s.setdefault("strategy_evaluations",{})
                    if key in observations and observations[key] != policy:
                        raise StateError("Conflicting same-checkpoint strategy exit evaluation")
                    observations[key] = policy; self._persist()
                if exits:
                    exit_check = exits[0]
                    action = dict(id=digest({"account":self.risk.s["identity"],"trade_id":trade.id,"kind":"STRATEGY_EXIT"})[:24],
                                  kind="STRATEGY_EXIT",target=source_id)
                elif reduction_inputs is not None:
                    from native_position_targets import build_position_targets
                    targets, daily, allocation = build_position_targets(self.provider,evidence,bot.strategy,reduction_inputs,now,
                        self.s.get("daily_position_targets",{}),apply_factor=policy is not None)
                    risk_reduction = next((a for a in decision.actions if a["kind"]=="REDUCE_EXPOSURE"),None)
                    if bot.strategy.__class__.__name__ == "F2Volatility24Native" and policy is None and risk_reduction is None:
                        raise RiskError("F2 voluntary reduction requires native stop/signal evaluation first")
                    target_quantity = dec(decimal_text(exchange.amount_to_contract_precision(trade.pair,float(targets[trade.pair]))))
                    if not 0 <= target_quantity <= targets[trade.pair]:
                        raise RiskError("Native target precision must not enlarge retained inventory")
                    allocation.update(rounded_target_quantity=str(target_quantity),trade_id=trade.id,state="READY")
                    quantity = dec(decimal_text(trade.amount))-target_quantity
                    minimum = exchange.get_min_pair_stake_amount(trade.pair,float(dec(limit)),bot.strategy.stoploss,1)
                    if quantity <= 0:
                        allocation["state"] = "NO_REDUCTION"
                    elif minimum is None or (quantity*dec(limit) < dec(decimal_text(minimum))
                            or (target_quantity>0 and target_quantity*dec(limit) < dec(decimal_text(minimum)))):
                        allocation["state"] = "BLOCKED_NATIVE_MINIMUM"
                    allocation["native_minimum_stake"] = None if minimum is None else decimal_text(minimum)
                    key = digest({"trade_id":trade.id,"strategy":allocation["strategy"],"cursor":evidence.snapshot.cursor})
                    records = self.s.setdefault("reduction_evaluations",{})
                    if key in records and records[key] != allocation:
                        raise StateError("Conflicting same-checkpoint position target")
                    self.s.setdefault("daily_position_targets",{}).update(daily)
                    records[key] = allocation; self._persist()
                    if allocation["state"] != "READY":
                        return None
                    action = dict(id=digest({"account":self.risk.s["identity"],"trade_id":trade.id,"kind":"POSITION_REDUCTION"})[:24],
                                  kind="POSITION_REDUCTION",target=source_id)
                    exit_check = ExitCheckTuple(ExitType.PARTIAL_EXIT)
                else:
                    return None
            previous = self.s["actions"].get(action["id"])
            if previous and (previous["state"] != "TERMINAL_RECONCILED" or (not previous["requires_further_exit"] and allocation is None)
                    or evidence.snapshot.cursor <= previous["reconciled_cursor"]):
                raise StateError("Exit action already attempted; query/reconcile before any retry")
            ordinal = 1 if previous is None else previous["attempt"]+1
            client_key = {"account":self.risk.s["identity"],"action":action["id"]}
            if ordinal > 1:
                client_key["attempt"] = ordinal
            amount = float(dec(decimal_text(trade.amount))-target_quantity); rate = float(dec(limit))
            if rate <= 0 or exchange.amount_to_precision(trade.pair,amount) != amount or exchange.price_to_precision(trade.pair,rate) != rate:
                raise RiskError("Exit precision changes explicit quantity or limit")
            request = dict(pair=trade.pair,ordertype="limit",side="sell",amount=amount,rate=rate,leverage=1,
                           reduceOnly=self.risk.market=="futures",time_in_force="GTC",initial_order=False)
            claimed = self.risk.claim_action(action["id"],now) if action["kind"] == "EXIT_POSITION" else copy.deepcopy(action)
            if risk_reduction is not None:
                claimed["rd1_parent_action"] = self.risk.claim_action(risk_reduction["id"],now)
            prior_funding = sum((dec(decimal_text(o.funding_fee)) for o in trade.orders if o.funding_fee is not None),D(0))
            total_funding = D(0) if trade.funding_fees is None else dec(decimal_text(trade.funding_fees))
            history = [] if previous is None else previous.get("prior_attempts",[])+[{k:copy.deepcopy(v) for k,v in previous.items() if k!="prior_attempts"}]
            ticket = dict(state="SUBMISSION_UNKNOWN",action=claimed,attempt=ordinal,prior_attempts=history,claimed_at=stamp(now),trade_id=trade.id,
                quantity_before=decimal_text(trade.amount),request=request,source_position_id=source_id,
                client_order_id="rd1x-"+digest(client_key)[:28],
                running_funding_credit=str(total_funding-prior_funding),last_filled_at=stamp(trade.date_last_filled_utc),
                checkpoint_hash=digest(checkpoint),native_binding=None,
                exit_type=exit_check.exit_type.value,strategy_evaluation=policy,
                target_remaining_quantity=str(target_quantity),reduction_evaluation=allocation)
            self.s["actions"][action["id"]] = ticket; self._persist()
            token = exchange.ticket.set(ticket)
            try:
                proxy = _ExitBot(bot,exchange,self,action["id"])
                tag = ("rd1_reduction" if risk_reduction is not None else "F2_daily_scale") if allocation is not None else (
                    ticket["client_order_id"] if policy is None else (policy["exit_tag"] if exit_check.exit_type == ExitType.EXIT_SIGNAL else None))
                result = FreqtradeBot.execute_trade_exit(proxy,trade,rate,exit_check,
                    exit_tag=tag,ordertype="limit",skip_custom_exit_price=True,
                    sub_trade_amt=amount if allocation is not None else None)
            finally:
                exchange.ticket.reset(token)
            if not result:
                Trade.session.rollback()  # discard uncommitted funding/metadata after a veto
                raise RiskError("Native exit did not complete submission; intent remains unknown")
            self.bind(action["id"],now)
            return action["id"]

    def bind(self,action_id,now):
        self._ready(now)
        if Trade.session.new or Trade.session.dirty or Trade.session.deleted:
            raise RiskError("Exit binding needs committed native rows")
        item = self.s["actions"][action_id]; envelope = self.envelope(action_id,now)
        trade = Trade.session.get(Trade,item["trade_id"])
        report = envelope.normalize(); order = trade.select_order_by_order_id(report["id"])
        if order is None or order.ft_order_tag != item["client_order_id"]:
            raise RiskError("Native exit row missing or tag differs; keep action pending")
        envelope.normalize(order)
        binding = {"trade_id":trade.id,"order_pk":order.id,"order_id":order.order_id}
        if item["native_binding"] not in (None,binding):
            raise RiskError("Native exit binding changed")
        item.update(native_binding=binding,state="NATIVE_BOUND_ACCOUNT_PENDING"); self._persist()
        return envelope

    def apply_filled(self,action_id,bot,now):
        """Apply a positive terminal report using the provider's durable fill fees."""
        with self._mutex:
            self._ready(now)
            item = self.s["actions"][action_id]; envelope = self.envelope(action_id,now)
            raw = json.loads(envelope.payload_json)
            if raw["status"]=="open" or dec(decimal_text(raw["filled"])) <= 0 or item["native_binding"] is None:
                raise RiskError("Positive terminal native exit binding required")
            fills = [f for f in self.provider.campaign_fills(item["source_position_id"]) if f.order_id==raw["id"]]
            quantity = sum((dec(f.quantity) for f in fills),D(0));cost = sum((dec(f.quantity)*dec(f.price) for f in fills),D(0))
            fees = sum((dec(f.fee_quote) for f in fills),D(0))
            if (any(f.side!="sell" or dec(f.fee_base)!=0 or f.received_at>envelope.received_at for f in fills)
                    or quantity != dec(decimal_text(raw["filled"])) or quantity<=0 or fees<=0
                    or raw.get("average") is None or abs(cost/quantity-dec(decimal_text(raw["average"])))>EPS
                    or raw.get("cost") is None or abs(cost-dec(decimal_text(raw["cost"])))>EPS):
                raise RiskError("Exit cumulative fill price/quantity/fees not proven")
            raw["fee"] = dict(currency="USDT",cost=float(fees),rate=float(fees/cost))
            derived = CapturedOrder(json.dumps(raw),envelope.received_at,"synthetic-exit-derived-complete-fees","synthetic")
            if type(bot.exchange) is not OfflineProtectionExchange or bot.wallets is not self.wallet.wallets:
                raise RiskError("Exit fill application needs offline protection and staged native wallet")
            self.wallet._check_native()
            self.s["actions"][action_id]["derived_fill_report"] = primitive(asdict(derived));self._persist()
            trade = Trade.session.get(Trade,item["trade_id"])
            result = self.gate.after_fill_guard.apply("exit-"+action_id+"-"+str(item["attempt"]),bot,trade,derived,now)
            item["state"]="NATIVE_FILLED_ACCOUNT_PENDING";self._persist()
            return result

    def reconcile(self,action_id,evidence,now):
        with self._mutex:
            self._ready(now); self.wallet.require_entry_clear(now)
            item=self.s["actions"][action_id]
            if item["state"] not in ("NATIVE_FILLED_ACCOUNT_PENDING","TERMINAL_RECONCILED"):
                raise StateError("Exit native fill application remains unconfirmed")
            # Other campaigns may still have open partial entries. Verify this
            # exit's committed protection without granting global entry permission.
            self.gate.after_fill_guard.verify_committed("exit-"+action_id+"-"+str(item["attempt"]))
            checkpoint=self.provider.s["checkpoints"].get(str(evidence.snapshot.cursor))
            if (checkpoint is None or checkpoint["output"] != primitive(asdict(evidence))
                    or self.provider.s["last_cursor"] != evidence.snapshot.cursor):
                raise RiskError("Exit reconciliation needs checked account evidence")
            trade=Trade.session.get(Trade,item["trade_id"]);order=trade.select_order_by_order_id(item["native_binding"]["order_id"])
            self.envelope(action_id,now).normalize(order)
            remaining=sum((dec(p.quantity) for p in evidence.snapshot.positions if p.position_id==item["source_position_id"]),D(0))
            if abs(remaining-(dec(item["quantity_before"])-dec(decimal_text(order.filled))))>EPS or order.ft_is_open:
                raise RiskError("Exit remaining inventory is not reconciled")
            decision=self.risk.evaluate(evidence.snapshot,now)
            item.update(state="TERMINAL_RECONCILED",remaining_quantity=str(remaining),risk_state=decision.state,
                        account_checkpoint_hash=digest(checkpoint),
                        requires_further_exit=remaining>dec(item.get("target_remaining_quantity","0"))+EPS,reconciled_cursor=evidence.snapshot.cursor)
            self._persist()
            return copy.deepcopy(item)
