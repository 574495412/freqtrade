"""Single-attempt RD1 cancellations, exclusively against a local Binance tape.

Raw transport reports remain separate from explicitly derived client identity
and fill fees. Dispatch, native application and account reconciliation are
separate durable phases; none is a cross-database atomic transaction.
"""
import copy
from dataclasses import asdict
from decimal import Decimal as D
import json
from pathlib import Path
import threading
from types import SimpleNamespace

from freqtrade.enums import TradingMode, MarginMode
from freqtrade.exchange.binance import Binance
from freqtrade.freqtradebot import FreqtradeBot
from freqtrade.persistence import Order, Trade
from native_entries import LocalBinanceTape
from native_after_fill import OfflineProtectionExchange
from native_evidence import CapturedOrder, decimal_text
from native_snapshot import EPS
from native_lifecycle import NativeLifecycle
from rd1 import RiskError, StateError, Store, aware, dec, digest, parse, primitive, stamp


class LocalCancelExchange(Binance):
    """Official cancel/query implementations, with zero hidden transport retries."""

    def __init__(self, transport, market):
        if type(transport) is not LocalBinanceTape or transport.allowed_methods != {"GET", "DELETE"}:
            raise RiskError("Cancellation requires the built-in GET/DELETE-only local tape")
        self._api = transport
        self._markets = transport.markets
        self._config = {"dry_run": False, "exchange": {"log_responses": False}}
        self._params = {}; self._is_demo_trading = False; self.log_responses = False
        self._ft_has = {"order_props_in_contracts": ["amount", "filled", "remaining", "cost"],
                        "exchange_has_overrides": {"fetchOrder": True}}
        self.trading_mode = TradingMode(market)
        self.margin_mode = MarginMode.ISOLATED if market == "futures" else MarginMode.NONE
        self.records = []

    def __del__(self):
        pass

    def close(self):
        self._api.session.close()

    def _capture(self, method, function, order_id, pair, params):
        record = {"method": method, "order_id": order_id, "pair": pair, "state": "UNKNOWN"}
        self.records.append(record)
        try:
            result = function(order_id, pair, params, count=0)
        except Exception as exc:
            record.update(error_type=type(exc).__name__, error=str(exc))
            raise
        raw, received = self._api.last_response
        record.update(state="RECEIVED", raw=copy.deepcopy(raw), ccxt=copy.deepcopy(result), received_at=stamp(received))
        return result

    def cancel_order(self, order_id, pair, params=None):
        return self._capture("DELETE", super().cancel_order, order_id, pair, params)

    def fetch_order(self, order_id, pair, params=None):
        return self._capture("GET", super().fetch_order, order_id, pair, params)

    def cancel_order_with_result(self, order_id, pair, amount):
        start = len(self.records)
        result = super().cancel_order_with_result(order_id, pair, amount)
        if not any(r.get("ccxt") == result for r in self.records[start:] if r["state"] == "RECEIVED"):
            raise RiskError("Native synthetic fallback is not a captured cancellation receipt")
        return result


class NativeCancellation:
    def __init__(self, directory, gate, *, create=False):
        if gate.after_fill_guard is None or not isinstance(gate.lifecycle, NativeLifecycle):
            raise RiskError("Cancellation requires native lifecycle and the after-fill entry barrier")
        self.gate = gate; self.risk = gate.lifecycle.risk
        self.engine = Trade.session.get_bind()
        self.store = Store(Path(directory), dict(self.risk.s["identity"], layer="native_cancellation_v1"), create=create)
        self._mutex = threading.RLock(); self._poisoned = False
        if create:
            self.s = {"schema_version": 1, "identity": self.store.identity, "sequence": 0, "requests": {}}
            self._persist()
        else:
            self.s = self.store.load()

    def close(self):
        self.store.close()

    def _ready(self):
        if self._poisoned or self.store.lock.closed:
            raise StateError("Cancellation writer unavailable")
        if not Trade.use_db or Trade.session.get_bind() is not self.engine:
            raise RiskError("Cancellation native database changed")
        if Trade.session.new or Trade.session.dirty or Trade.session.deleted:
            raise RiskError("Cancellation requires committed native inventory")

    def _persist(self):
        if self._poisoned or self.store.lock.closed:
            raise StateError("Cancellation writer unavailable")
        self.s["sequence"] += 1
        try:
            self.store.save(self.s)
        except Exception as exc:
            self._poisoned = True
            raise StateError("Cancellation write not acknowledged; reopen and reconcile") from exc

    def _envelope(self, request_id, now):
        item = self.s["requests"][request_id]
        result = copy.deepcopy(item["result"])
        record = next(r for r in reversed(item["transport"]) if r.get("ccxt") == result)
        received = parse(record["received_at"])
        if not parse(item["claimed_at"]) <= received <= aware(now) or (aware(now)-received).total_seconds() > 15:
            raise RiskError("Cancellation receipt is stale, future or predates its claim")
        if item.get("queries") and received < parse(item["queries"][-1]["at"]):
            raise RiskError("Recovery receipt predates query")
        entry = self.gate.s["requests"][request_id]
        original = entry["ticket"]["client_order_id"]
        expected = entry["ccxt_payload"]
        last_filled = dec(item["prior_filled"]); last_received = parse(item["claimed_at"])
        for prior in item["transport"]:
            if prior["state"] != "RECEIVED":
                continue
            payload = prior["ccxt"]
            qty = dec(decimal_text(payload.get("filled")))
            at = parse(prior["received_at"])
            if (payload.get("id") != expected["id"] or payload.get("symbol") != expected["symbol"]
                    or qty < last_filled or at < last_received):
                raise RiskError("Cancellation transport identity, cumulative fills or receipt chronology regressed")
            last_filled, last_received = qty, at
        alias = result.get("clientOrderId")
        if alias != original:
            # Spot DELETE may rename clientOrderId. Preserve and prove the link,
            # including a later GET carrying that alias; never silently rewrite raw.
            linked = any(r["state"] == "RECEIVED" and r["method"] == "DELETE"
                and r["raw"].get("origClientOrderId") == original
                and r["ccxt"].get("clientOrderId") == alias
                and r["ccxt"].get("id") == expected["id"]
                and r["ccxt"].get("symbol") == expected["symbol"] for r in item["transport"])
            if not linked or not alias:
                raise RiskError("Cancellation client identity has no captured original link")
        result["clientOrderId"] = original
        envelope = CapturedOrder(json.dumps(result,sort_keys=True), received, "synthetic-cancel-derived-original-identity", "synthetic")
        report = envelope.normalize()
        if (report["id"] != expected["id"] or report["symbol"] != expected["symbol"]
                or report["side"] != "buy" or dec(report["amount"]) != dec(decimal_text(expected["amount"]))
                or dec(report["price"]) != dec(decimal_text(expected["price"]))):
            raise RiskError("Cancellation response differs from the reserved native order")
        filled, remaining, amount = (dec(report[k]) for k in ("filled", "remaining", "amount"))
        if (filled < dec(item["prior_filled"]) or remaining < 0 or filled > amount
                or abs(filled+remaining-amount) > EPS
                or report["status"] not in ("open", "canceled", "closed", "expired", "rejected")
                or (report["status"] == "closed" and remaining != 0)):
            raise RiskError("Cancellation cumulative quantity or status is inconsistent")
        return envelope

    def dispatch(self, request_id, evidence, exchange, now):
        """Claim once, call official cancel/query, retain risk pending finalization."""
        with self._mutex:
            self._ready(); self.gate.require_account_batch_clear()
            self.gate.require_research_driver(now,'dispatch')
            if request_id in self.s["requests"]:
                raise StateError("Cancellation already attempted; query and reconcile, never redispatch")
            if type(exchange) is not LocalCancelExchange or exchange.trading_mode.value != self.risk.market:
                raise RiskError("Wrong isolated cancellation exchange")
            if evidence.evidence_class != "synthetic":
                raise RiskError("Local cancellation cannot certify captured account activity")
            snap = self.gate.bridge.validate(evidence, now, valuation_required=False)
            entry = self.gate.s["requests"][request_id]
            binding = entry.get("native_binding") or self.gate.bind_native(request_id)
            identity = (entry["ccxt_payload"]["symbol"], entry["ccxt_payload"]["id"])
            source_id = next((k for k, v in evidence.order_bindings.items() if tuple(v) == identity), None)
            trade = Trade.session.get(Trade, binding["trade_id"])
            if trade is None or trade.pair != identity[0]:
                raise RiskError("Cancellation bound native trade is missing or changed")
            order = trade.select_order_by_order_id(identity[1])
            if (source_id is None or order is None or order.id != binding["order_pk"]
                    or not order.ft_is_open or len(trade.orders) != 1):
                raise RiskError("Cancellation requires one bound initial open entry")
            # Preserve the pinned native handler's dust refusal at dispatch time.
            minimum = exchange.get_min_pair_stake_amount(trade.pair, trade.open_rate, -.05)
            if order.safe_filled > 0 and minimum and (trade.amount+order.safe_filled)*trade.open_rate < minimum:
                raise RiskError("Native minimum stake refuses cancellation of an unexitable partial")
            decision = self.risk.evaluate(snap, now)
            action = next((a for a in decision.actions if a["kind"] == "CANCEL_ENTRY" and a["target"] == source_id), None)
            if action is None:
                raise RiskError("Frozen RD1 has no cancellation action yet")
            controller=dict(identity=self.store.identity,directory=str(self.store.directory.resolve()))
            required=self.gate.s.get('required_cancel_controller')
            if required is not None and required!=controller:
                raise RiskError('Another cancellation journal is registered for this account')
            self.gate.s['required_cancel_controller']=controller;self.gate._persist()
            claimed = self.risk.claim_action(action["id"], now)
            self.s["requests"][request_id] = {"state": "DISPATCH_UNKNOWN", "action": claimed,
                "claimed_at": stamp(now), "trade_id": trade.id, "prior_filled": decimal_text(order.safe_filled),
                "account_input": primitive(asdict(evidence)), "transport": []}
            self._persist()
            start = len(exchange.records)
            try:
                result = exchange.cancel_order_with_result(identity[1], identity[0], trade.amount)
            except Exception:
                self.s["requests"][request_id]["transport"] = copy.deepcopy(exchange.records[start:])
                self._persist()
                raise
            item = self.s["requests"][request_id]
            item.update(transport=copy.deepcopy(exchange.records[start:]), result=copy.deepcopy(result), state="RECEIPT_PENDING")
            self._persist()
            return self._envelope(request_id, parse(item["transport"][-1]["received_at"]))

    def query(self, request_id, exchange, now):
        """Recover a lost or open result by GET only; never resend cancellation."""
        with self._mutex:
            self._ready(); self.gate.require_account_batch_clear()
            item = self.s["requests"][request_id]
            if item["state"] not in ("DISPATCH_UNKNOWN", "RECEIPT_PENDING"):
                raise StateError("Cancellation has already entered native application")
            if type(exchange) is not LocalCancelExchange or exchange.trading_mode.value != self.risk.market:
                raise RiskError("Wrong isolated cancellation exchange")
            if aware(now) < parse(item["claimed_at"]):
                raise RiskError("Recovery query predates cancellation")
            item.setdefault("queries", []).append({"at": stamp(now), "state": "UNKNOWN"})
            self._persist()
            original = self.gate.s["requests"][request_id]["ccxt_payload"]
            start = len(exchange.records)
            try:
                result = exchange.fetch_order(original["id"], original["symbol"])
            except Exception:
                item["transport"].extend(copy.deepcopy(exchange.records[start:]))
                self._persist()
                raise
            item["transport"].extend(copy.deepcopy(exchange.records[start:]))
            item.update(result=copy.deepcopy(result), state="RECEIPT_PENDING")
            item["queries"][-1]["state"] = "RECEIVED"
            self._persist()
            received = parse(item["transport"][-1]["received_at"])
            if received < aware(now):
                raise RiskError("Recovery receipt predates query")
            return self._envelope(request_id, received)

    def apply_terminal(self, request_id, bot, now, *, fills, fills_complete):
        """Run native terminal cancel handling with proven cumulative fill fees.

Only initial entries with quote fees are supported. Whole account evidence must
subsequently pass reconcile(); native updates alone never release capacity.
"""
        with self._mutex:
            self._ready()
            if type(bot.exchange) is not OfflineProtectionExchange or bot.exchange.trading_mode.value != self.risk.market:
                raise RiskError("Only offline native protection application is supported")
            item = self.s["requests"][request_id]
            if item["state"] != "RECEIPT_PENDING":
                raise StateError("Cancellation application already attempted or no receipt")
            envelope = self._envelope(request_id, now); raw = json.loads(envelope.payload_json)
            if raw["status"] == "open":
                raise RiskError("Cancellation has no terminal receipt")
            if fills_complete is not True:
                raise RiskError("Complete cancellation fill ledger required")
            fills = tuple(fills); seen = set(); quantity = D(0); cost = D(0); fees = D(0)
            previous = None; previous_received = None
            entry_at = parse(self.gate.s["requests"][request_id]["claimed_at"])
            for index, fill in enumerate(fills, 1):
                if (fill.order_id != raw["id"] or fill.pair != raw["symbol"] or fill.side != "buy"
                        or fill.evidence_class != "synthetic" or not fill.source_id or not fill.fill_id
                        or fill.fill_id in seen or fill.sequence != index
                        or not entry_at <= aware(fill.at) <= aware(fill.received_at) <= envelope.received_at
                        or (previous is not None and (aware(fill.at) < previous or aware(fill.received_at) < previous_received))
                        or dec(fill.quantity) <= 0 or dec(fill.price) <= 0
                        or dec(fill.fee_base) != 0 or dec(fill.fee_quote) < 0):
                    raise RiskError("Cancellation fill identity, ordering, chronology or fee unsupported")
                seen.add(fill.fill_id); previous = aware(fill.at); previous_received = aware(fill.received_at)
                quantity += dec(fill.quantity); cost += dec(fill.quantity)*dec(fill.price); fees += dec(fill.fee_quote)
            if abs(quantity-dec(decimal_text(raw["filled"]))) > EPS:
                raise RiskError("Cancellation fill ledger misses cumulative fills")
            if quantity:
                if (raw.get("average") is None or abs(cost/quantity-dec(decimal_text(raw["average"]))) > EPS
                        or raw.get("cost") is None or abs(cost-dec(decimal_text(raw["cost"]))) > EPS or fees <= 0):
                    raise RiskError("Cancellation average, cost or positive quote fee unavailable")
                raw["fee"] = {"currency": "USDT", "cost": float(fees), "rate": float(fees/cost)}
            trade = Trade.session.get(Trade, item["trade_id"])
            if trade is None or len(trade.orders) != 1:
                raise RiskError("Cancellation native campaign disappeared or expanded")
            order = trade.select_order_by_order_id(raw["id"])
            if (order is None or order.safe_filled > float(quantity) or not order.ft_is_open
                    or order.ft_order_side != "buy" or trade.is_short or trade.leverage != 1
                    or trade.trading_mode.value != self.risk.market or trade.pair != raw["symbol"]
                    or dec(decimal_text(order.amount)) != dec(decimal_text(raw["amount"]))
                    or dec(decimal_text(order.price)) != dec(decimal_text(raw["price"]))
                    or order.safe_fee_base != 0 or trade.amount != 0):
                raise RiskError("Cancellation native initial entry changed, regressed or has unsupported fees")
            derived = CapturedOrder(json.dumps(raw), envelope.received_at, "synthetic-cancel-derived-fill-fees", "synthetic")
            event = "cancel-" + digest({"request": request_id, "claim": item["claimed_at"]})[:24]
            item.update(state="APPLICATION_PENDING", protection_event=event, fills=primitive([asdict(f) for f in fills]),
                        derived_envelope=primitive(asdict(derived)))
            self._persist()
            def update(trade_arg, order_id, report):
                if trade_arg is not trade or order_id != raw["id"] or report != raw:
                    raise RiskError("Native cancellation changed the protected update")
                # The terminal handler sets ft_cancel_reason before its update.
                # Persist that native metadata before entering the guarded phase.
                Trade.commit()
                return self.gate.after_fill_guard.apply(event, bot, trade, derived, now)
            # No RPC or wallet transport at this orchestration boundary. Actual
            # after-fill wallet handling remains the explicit offline bot boundary.
            proxy = SimpleNamespace(exchange=bot.exchange, strategy=bot.strategy,
                update_trade_state=update, wallets=SimpleNamespace(update=lambda: None),
                _notify_enter_cancel=lambda *a, **kw: None)
            fully_canceled = FreqtradeBot.handle_cancel_enter(proxy, trade, raw, order, "frozen RD1 cancellation", replacing=False)
            Trade.commit()
            item.update(state="NATIVE_APPLIED_ACCOUNT_PENDING", native_fully_canceled=fully_canceled)
            self._persist()
            return derived

    def recover_applied(self, request_id):
        """Verify durable native effects after acknowledgement loss; no callbacks."""
        with self._mutex:
            self._ready()
            item = self.s["requests"][request_id]
            if item["state"] != "APPLICATION_PENDING":
                raise StateError("No uncertain native application to recover")
            raw = json.loads(item["derived_envelope"]["payload_json"])
            if dec(decimal_text(raw["filled"])):
                self.gate.after_fill_guard.verify_committed(item["protection_event"])
            else:
                if (Trade.session.get(Trade, item["trade_id"]) is not None
                        or Trade.session.query(Order).filter(Order.ft_pair == raw["symbol"], Order.order_id == raw["id"]).count()):
                    raise RiskError("Zero-fill native cleanup is not committed")
            item.update(state="NATIVE_APPLIED_ACCOUNT_PENDING", recovery="committed_native_effects_checked_without_repeating_callbacks")
            self._persist()

    def reconcile(self, request_id, evidence, now, *, fills):
        with self._mutex:
            self._ready()
            item = self.s["requests"][request_id]
            if item["state"] not in ("NATIVE_APPLIED_ACCOUNT_PENDING", "TERMINAL_RECONCILED"):
                raise StateError("Native cancellation application is not confirmed")
            fills = tuple(fills)
            if primitive([asdict(f) for f in fills]) != item["fills"]:
                raise RiskError("Cancellation fill ledger changed after application")
            data = item["derived_envelope"]
            envelope = CapturedOrder(data["payload_json"], parse(data["received_at"]), data["source_id"], data["evidence_class"])
            if dec(decimal_text(json.loads(envelope.payload_json)["filled"])):
                self.gate.after_fill_guard.verify_committed(item["protection_event"])
            result = self.gate.reconcile_native(request_id, "cancel-reconcile-"+item["action"]["id"], envelope, evidence, now,
                fills=fills, fills_complete=True, sequence_basis="source_ordered")
            item["state"] = result["state"]
            self._persist()
            return result
