"""Isolated native entry request path; deliberately has no network transport.

Calls the installed Freqtrade and CCXT request builders, backed exclusively by
explicit synthetic Binance responses. This is not a production Exchange class.
Risk capacity stays pending until complete native lifecycle reconciliation.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
import copy
import json
from pathlib import Path
import threading

import ccxt
from freqtrade.enums import TradingMode, MarginMode
from freqtrade.exchange.binance import Binance
from freqtrade.persistence import Order, Trade

from native_snapshot import NativeSnapshotBridge
from native_evidence import CapturedOrder, decimal_text
from rd1 import RiskError, StateError, Store, dec, stamp, parse, primitive, digest


class LocalBinanceTape(ccxt.binance):
    """Real CCXT builder/parser; fetch2 only consumes explicit local responses."""

    def __init__(self, markets, responses, *, allowed_methods=("POST",)):
        super().__init__({"enableRateLimit": False})
        self.set_markets(copy.deepcopy(markets))
        self.responses = list(responses)
        self.requests = []
        self.last_response = None
        self.allowed_methods = frozenset(allowed_methods)
        if not self.allowed_methods or not self.allowed_methods <= {"POST", "GET", "DELETE"}:
            raise RiskError("Unsupported local order transport methods")

    def fetch2(self, path, api="public", method="GET", params=None, *args, **kwargs):
        if path != "order" or api not in (["private"], ["fapiPrivate"]) or method not in self.allowed_methods:
            raise RiskError(f"Unsupported local synthetic order request: {path!r}, {api!r}, {method!r}")
        self.last_response = None
        self.requests.append(dict(path=path, api=copy.deepcopy(api), method=method, params=copy.deepcopy(params)))
        if not self.responses:
            raise RiskError("Synthetic response tape exhausted")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        # Explicit event time supplied by the fixture; no wall-clock fabrication.
        payload, received_at = response
        self.last_response = (copy.deepcopy(payload), received_at)
        return copy.deepcopy(payload)

    def fetch(self, *args, **kwargs):
        raise RiskError("Network transport forbidden in isolated entry adapter")


class ReplayEntryExchange(Binance):
    """Official create_order implementation with a scoped clientOrderId hook."""

    def __init__(self, transport, gate, market):
        if type(transport) is not LocalBinanceTape:
            raise RiskError("Only the built-in local response tape is allowed")
        if market != gate.lifecycle.risk.market:
            raise RiskError("Entry exchange market differs from RD1")
        # Native Exchange init would create network clients. No such init is run.
        self._api = transport
        self._markets = transport.markets
        self._config = {"dry_run": False, "exchange": {"log_responses": False}}
        self._params = {}
        self._is_demo_trading = False
        self.log_responses = False
        self._ft_has = {"order_props_in_contracts": ["amount", "filled", "remaining", "cost"]}
        self.trading_mode = TradingMode(market)
        self.margin_mode = MarginMode.ISOLATED if market == "futures" else MarginMode.NONE
        self.gate = gate
        self._entry = ContextVar("isolated_rd1_entry", default=None)
        self._ticket = ContextVar("isolated_rd1_ticket", default=None)
        self.leverage_preparations = []

    def __del__(self):
        # There are no native async clients or background event loop to close.
        pass

    def close(self):
        self._api.session.close()

    @contextmanager
    def for_entry(self, request_id, evidence, proposal, now):
        if self._entry.get() is not None:
            raise StateError("Nested entry context forbidden")
        token = self._entry.set((request_id, evidence, proposal, now))
        try:
            yield
        finally:
            self._entry.reset(token)

    def create_order(self, **kwargs):
        context = self._entry.get()
        if context is None:
            raise RiskError("No explicit RD1 entry context")
        return self.gate.submit(self, *context, **kwargs)

    def _get_params(self, side, ordertype, leverage, reduceOnly, time_in_force="GTC"):
        ticket = self._ticket.get()
        if ticket is None:
            raise StateError("No durably claimed entry attempt")
        params = super()._get_params(side, ordertype, leverage, reduceOnly, time_in_force)
        params["clientOrderId"] = ticket["client_order_id"]
        return params

    def _lev_prep(self, pair, leverage, side, accept_fail=False):
        # Synthetic boundary: no margin-mode or leverage request is sent.
        if leverage != 1:
            raise RiskError("Only 1x isolated research supported")
        self.leverage_preparations.append((pair, leverage, side, accept_fail))

    def _native_create(self, ticket, kwargs):
        token = self._ticket.set(ticket)
        try:
            return super().create_order(**kwargs)
        finally:
            self._ticket.reset(token)


class NativeEntryGate:
    def __init__(self, directory, lifecycle, *, create=False, after_fill_guard=None):
        self.lifecycle = lifecycle
        self.bridge = NativeSnapshotBridge(lifecycle)
        if after_fill_guard is not None and after_fill_guard.identity != lifecycle.risk.s["identity"]:
            raise RiskError("Protection guard account identity differs")
        self.after_fill_guard = after_fill_guard
        self.runtime_barrier = None
        self._mutex = threading.RLock()
        self._poisoned = False
        self.store = Store(Path(directory), dict(lifecycle.s["identity"], layer="native_entry_receipts_v1"), create=create)
        if create:
            self.s = {"schema_version": 1, "sequence": 0, "identity": self.store.identity, "requests": {}}
            self._persist()
        else:
            self.s = self.store.load()

    def close(self):
        self.store.close()

    def bind_runtime_barrier(self, barrier):
        """Register the required wallet/account bootstrap durably before use."""
        from native_wallets import NativeWalletBridge
        with self._mutex:
            if (type(barrier) is not NativeWalletBridge or barrier.provider.bridge.reservations is not self.lifecycle
                    or self.after_fill_guard is None):
                raise RiskError("Matching native wallet provider and protection guard required")
            required = copy.deepcopy(barrier.store.identity)
            if self.s.get("required_runtime_barrier") not in (None, required):
                raise StateError("Registered runtime barrier identity changed")
            self.s["required_runtime_barrier"] = required
            self._persist()
            self.runtime_barrier = barrier

    def _persist(self):
        if self._poisoned or self.store.lock.closed:
            raise StateError("Entry receipt writer unavailable")
        self.s["sequence"] += 1
        try:
            self.store.save(self.s)
        except Exception as exc:
            self._poisoned = True
            raise StateError("Entry receipt not durably acknowledged; reconcile") from exc

    def require_account_batch_clear(self):
        if self._poisoned or self.store.lock.closed:
            raise StateError("Entry receipt writer unavailable")
        if self.s.get("pending_account_batch") is not None:
            raise RiskError("Complete the pending whole-account observation batch before entry")

    def _check_request(self, exchange, proposal, kwargs):
        expected = dict(pair=proposal.pair, ordertype="limit", side="buy",
                        amount=float(proposal.quantity), rate=float(proposal.price), leverage=1,
                        time_in_force="GTC", reduceOnly=False, initial_order=True)
        if kwargs != expected or dec(proposal.quantity) <= 0 or dec(proposal.leverage) != 1:
            raise RiskError("Native request differs from frozen initial 1x long limit proposal")
        pair = proposal.pair
        contracts = exchange.amount_to_precision(pair, exchange._amount_to_contracts(pair, kwargs["amount"]))
        quantity = exchange._contracts_to_amount(pair, contracts)
        price = exchange.price_to_precision(pair, kwargs["rate"])
        if dec(decimal_text(quantity)) != dec(proposal.quantity) or dec(decimal_text(price)) != dec(proposal.price):
            raise RiskError("Native precision changes reserved quantity or price")
        # Metadata is explicit synthetic input here, not certified historical rules.
        market = exchange.markets[pair]
        step = dec(decimal_text(exchange.get_precision_amount(pair))) * dec(decimal_text(exchange.get_contract_size(pair)))
        if exchange.precisionMode != ccxt.TICK_SIZE or step != dec(proposal.amount_step):
            raise RiskError("Native amount step differs from RD1 evidence")
        minimum = market["limits"]["cost"]["min"]
        if minimum is None or dec(decimal_text(minimum)) > dec(proposal.minimum_notional):
            raise RiskError("Native minimum notional absent or stricter than RD1 evidence")
        for kind, value in (("amount", contracts), ("price", price), ("cost", quantity * price)):
            limits = market["limits"].get(kind, {})
            for bound, compare in (("min", lambda a, b: a < b), ("max", lambda a, b: a > b)):
                if limits.get(bound) is not None and compare(dec(decimal_text(value)), dec(decimal_text(limits[bound]))):
                    raise RiskError("Native market limit violated: " + kind + "." + bound)

    def _validate_response(self, item):
        ticket = item["ticket"]
        proposal = ticket["proposal"]
        received_at = parse(item["received_at"])
        report = CapturedOrder(json.dumps(item["ccxt_payload"]), received_at, "local-binance-tape", "synthetic").normalize()
        if (report["clientOrderId"] != ticket["client_order_id"] or report["symbol"] != proposal["pair"]
                or report["side"] != "buy" or dec(report["amount"]) != dec(proposal["quantity"])
                or dec(report["price"]) != dec(proposal["price"]) or received_at < parse(item["claimed_at"])):
            raise RiskError("Original response differs from claimed request")
        if (report["status"] not in ("open", "closed", "canceled", "expired", "rejected")
                or dec(report["filled"]) < 0 or dec(report["remaining"]) < 0
                or dec(report["filled"]) + dec(report["remaining"]) != dec(report["amount"])):
            raise RiskError("Original response has inconsistent status or quantities")
        return report

    def submit(self, exchange, request_id, evidence, proposal, now, **kwargs):
        with self._mutex:
            self.require_account_batch_clear()
            if self._poisoned or self.store.lock.closed:
                raise StateError("Entry receipt writer unavailable")
            required = self.s.get("required_runtime_barrier")
            if required is not None and (self.runtime_barrier is None or self.runtime_barrier.store.identity != required):
                raise StateError("Registered runtime barrier must be reattached before entry")
            if self.runtime_barrier is not None:
                self.runtime_barrier.require_entry_clear(now)
            if self.after_fill_guard is not None:
                self.after_fill_guard.require_entry_clear()
            if evidence.evidence_class != "synthetic":
                raise RiskError("Local tape cannot certify captured exchange activity")
            self._check_request(exchange, proposal, kwargs)
            reserved = self.bridge.reserve(request_id, evidence, proposal, now)
            if not reserved["reserved"]:
                raise RiskError("RD1 entry rejected: " + str(reserved["reasons"]))
            ticket = self.lifecycle.claim_for_replay(request_id, evidence.snapshot, now)
            self.s["requests"][request_id] = {
                "ticket": ticket, "request": copy.deepcopy(kwargs),
                "state": "SUBMISSION_UNKNOWN", "claimed_at": stamp(now), "native_binding": None}
            self._persist()  # before reaching even the local request transport
            order = exchange._native_create(ticket, kwargs)
            raw, received_at = exchange._api.last_response
            item = self.s["requests"][request_id]
            item.update(state="RESPONSE_CAPTURED", venue_payload=raw,
                        ccxt_payload=copy.deepcopy(order), received_at=stamp(received_at), evidence_class="synthetic")
            self._persist()  # preserve response even if subsequent validation fails
            self._validate_response(item)
            return order

    def bind_native(self, request_id):
        """Join captured response to actual committed native Order/Trade rows.

        Binding never releases risk capacity or asserts after-fill completion.
        """
        with self._mutex:
            if Trade.session.get_bind() is not self.bridge.engine or Trade.session.new or Trade.session.dirty or Trade.session.deleted:
                raise RiskError("Native DB binding changed or uncommitted")
            item = self.s["requests"][request_id]
            raw = item.get("ccxt_payload")
            if raw is None:
                raise RiskError("No authoritative response to bind")
            self._validate_response(item)
            rows = Trade.session.query(Order).filter(Order.ft_pair == raw["symbol"], Order.order_id == raw["id"]).all()
            if len(rows) != 1:
                raise RiskError("Native order missing or ambiguous; keep pending")
            CapturedOrder(json.dumps(raw), parse(item["received_at"]), "local-binance-tape", "synthetic").normalize(rows[0])
            binding = {"trade_id": rows[0].ft_trade_id, "order_pk": rows[0].id,
                       "pair": rows[0].ft_pair, "venue_order_id": rows[0].order_id}
            if item["native_binding"] not in (None, binding):
                raise StateError("Native binding changed")
            item["native_binding"] = binding
            self._persist()
            return copy.deepcopy(binding)

    def reconcile_native(self, request_id, event_id, envelope, evidence, now, *, fills, fills_complete, sequence_basis):
        """Persist report inputs before handing off to native lifecycle release.

        These stores are separate transactions. Acknowledgement failure keeps the
        writer poisoned; replaying identical evidence after reopening is idempotent.
        """
        from native_lifecycle import NativeLifecycle
        fills = tuple(fills)
        evidence = copy.deepcopy(evidence)
        with self._mutex:
            if self._poisoned or self.store.lock.closed:
                raise StateError("Entry receipt writer unavailable")
            if not isinstance(self.lifecycle, NativeLifecycle):
                raise RiskError("Native lifecycle required for capacity reconciliation")
            if envelope.evidence_class != "synthetic" or evidence.evidence_class != "synthetic":
                raise RiskError("Local entry tape cannot be upgraded to captured exchange evidence")
            if not isinstance(event_id, str) or not event_id:
                raise RiskError("Reconciliation event identity required")
            item = self.s["requests"][request_id]
            payload = primitive({"envelope": asdict(envelope), "evidence": asdict(evidence),
                                 "fills": [asdict(f) for f in fills], "fills_complete": fills_complete,
                                 "sequence_basis": sequence_basis})
            signature = digest(payload)
            events = item.setdefault("lifecycle_reports", {})
            if event_id in events and events[event_id]["signature"] != signature:
                raise StateError("Conflicting native receipt event")
            if event_id not in events:
                events[event_id] = {"signature": signature, "payload": payload, "result": None}
                self._persist()
            result = self.lifecycle.reconcile_native(request_id, event_id, envelope, evidence, now,
                fills=fills, fills_complete=fills_complete, sequence_basis=sequence_basis)
            events[event_id]["result"] = result["state"]
            self._persist()
            return result
