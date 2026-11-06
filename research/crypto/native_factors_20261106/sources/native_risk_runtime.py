"""Explicit event-driven cancellation-to-exit coordination in local research.

The existing account, protection, lifecycle, cancellation and exit journals are
authoritative. This coordinator adds no shadow Trade, inferred receipts, private
transport or financial strategy activation. Callers supply every source event.
"""
from dataclasses import asdict
from decimal import Decimal as D
import json

from freqtrade.persistence import Trade
from native_cancellations import NativeCancellation
from native_exits import NativeRiskExit
from native_evidence import CapturedOrder, decimal_text
from native_snapshot import EPS
from rd1 import RiskError, StateError, dec, digest


class NativeRiskRuntime:
    def __init__(self, gate, wallet, cancellations, exits):
        if (gate.runtime_barrier is not wallet or gate.after_fill_guard is None
                or type(cancellations) is not NativeCancellation or cancellations.gate is not gate
                or type(exits) is not NativeRiskExit or exits.gate is not gate
                or exits.wallet is not wallet):
            raise RiskError("Risk runtime requires one registered native account/lifecycle")
        self.gate = gate; self.wallet = wallet; self.provider = wallet.provider
        self.cancellations = cancellations; self.exits = exits
        self.guard = gate.after_fill_guard; self.risk = gate.lifecycle.risk

    def _entry(self, request_id, now):
        self.wallet.clock.require(now)
        self.cancellations._ready(); self.exits._ready(now)
        entry = self.gate.s["requests"][request_id]
        binding = entry.get("native_binding")
        if binding is None:
            raise RiskError("Runtime needs an already bound initial native entry")
        return entry, binding

    def _fills(self, trade_id):
        campaigns = {e["campaign_id"] for e in self.provider.s["events"] if e["native_trade_id"] == trade_id}
        if len(campaigns) > 1:
            raise RiskError("Native entry has ambiguous source campaign")
        return () if not campaigns else self.provider.campaign_fills(next(iter(campaigns)))

    def _confirm(self, evidence, quote_receipts, now, *, complete_sequence,
                 complete_from_inception, funding_complete):
        checked, audit = self.provider.checkpoint(evidence, quote_receipts, now,
            complete_sequence=complete_sequence, complete_from_inception=complete_from_inception,
            funding_complete=funding_complete)
        self.wallet.confirm(checked, now)
        return checked, audit

    def observe_partial(self, request_id, report, evidence, receipt, quote_receipts, bot, now,
                        *, complete_sequence, complete_from_inception, funding_complete):
        """Record an actual open partial, including fees, without certifying protection."""
        entry, binding = self._entry(request_id, now)
        if request_id in self.cancellations.s["requests"]:
            raise StateError("Use cancellation query once cancellation is attempted")
        if report.evidence_class != "synthetic" or not report.source_id:
            raise RiskError("Partial runtime requires an explicit synthetic source receipt")
        raw = json.loads(report.payload_json); expected = entry["ccxt_payload"]
        if (raw.get("status") != "open" or dec(decimal_text(raw.get("filled"))) <= 0
                or any(raw.get(k) != expected.get(k) for k in ("id", "symbol", "side", "amount", "price", "clientOrderId"))):
            raise RiskError("Open partial differs from the claimed native entry")
        fills = self._fills(binding["trade_id"])
        quantity = sum((dec(f.quantity) for f in fills), D(0))
        cost = sum((dec(f.quantity)*dec(f.price) for f in fills), D(0))
        fees = sum((dec(f.fee_quote) for f in fills), D(0))
        if (any(f.order_id != raw["id"] or f.side != "buy" or dec(f.fee_base) != 0
                or f.received_at > report.received_at for f in fills)
                or quantity != dec(decimal_text(raw["filled"])) or quantity <= 0 or fees <= 0
                or raw.get("average") is None or abs(cost/quantity-dec(decimal_text(raw["average"]))) > EPS
                or raw.get("cost") is None or abs(cost-dec(decimal_text(raw["cost"]))) > EPS):
            raise RiskError("Open partial lacks complete ordered quantity/price/fee evidence")
        raw["fee"] = dict(currency="USDT", cost=float(fees), rate=float(fees/cost))
        derived = CapturedOrder(json.dumps(raw), report.received_at, "synthetic-partial-derived-fees", "synthetic")
        event = "partial-"+digest({"request": request_id, "report": asdict(derived)})[:24]
        bot.wallets = self.wallet.stage(event, receipt, evidence, now)
        trade = Trade.session.get(Trade, binding["trade_id"])
        protection = self.guard.apply(event, bot, trade, derived, now, return_pending_partial=True)
        if protection["state"] != "OPEN_PARTIAL_PENDING":
            raise RiskError("Open entry unexpectedly acquired terminal protection")
        checked, audit = self._confirm(evidence, quote_receipts, now, complete_sequence=complete_sequence,
            complete_from_inception=complete_from_inception, funding_complete=funding_complete)
        lifecycle = self.gate.reconcile_native(request_id, event, report, checked, now,
            fills=fills, fills_complete=True, sequence_basis="source_ordered")
        return dict(evidence=checked, audit=audit, lifecycle=lifecycle, protection=protection,
                    decision=self.risk.evaluate(checked.snapshot, now))

    def cancel(self, request_id, evidence, exchange, now):
        """A pending protection barrier must not suppress a valid risk cancellation."""
        self._entry(request_id, now)
        return self.cancellations.dispatch(request_id, evidence, exchange, now)

    def query_cancel(self, request_id, exchange, now):
        self._entry(request_id, now)
        return self.cancellations.query(request_id, exchange, now)

    def finish_cancel(self, request_id, evidence, receipt, quote_receipts, bot, now,
                      *, complete_sequence, complete_from_inception, funding_complete):
        """Resume from child durable phases; do not repeat terminal native callbacks."""
        _, binding = self._entry(request_id, now)
        item = self.cancellations.s["requests"][request_id]
        if item["state"] not in ("RECEIPT_PENDING", "APPLICATION_PENDING",
                                 "NATIVE_APPLIED_ACCOUNT_PENDING", "TERMINAL_RECONCILED"):
            raise StateError("Cancellation needs an explicit terminal query receipt")
        report = self.cancellations._envelope(request_id, now)
        if report.normalize()["status"] == "open":
            raise RiskError("Cancellation remains open; no native terminal application")
        fills = self._fills(binding["trade_id"])
        event = "cancel-account-"+digest({"request":request_id,"evidence":asdict(evidence)})[:24]
        bot.wallets = self.wallet.stage(event, receipt, evidence, now)
        if item["state"] == "RECEIPT_PENDING":
            self.cancellations.apply_terminal(request_id, bot, now, fills=fills, fills_complete=True)
        elif item["state"] == "APPLICATION_PENDING":
            self.cancellations.recover_applied(request_id)
        checked, audit = self._confirm(evidence, quote_receipts, now, complete_sequence=complete_sequence,
            complete_from_inception=complete_from_inception, funding_complete=funding_complete)
        lifecycle = self.cancellations.reconcile(request_id, checked, now, fills=fills)
        return dict(evidence=checked, audit=audit, lifecycle=lifecycle,
                    decision=self.risk.evaluate(checked.snapshot, now))

    def exit_after_cancel(self, request_id, checked, exchange, bot, now, *, limit):
        """Only native inventory proven by completed cancellation may be exited."""
        _, binding = self._entry(request_id, now)
        item = self.cancellations.s["requests"].get(request_id)
        if item is None or item["state"] != "TERMINAL_RECONCILED":
            raise StateError("Entry cancellation is not terminally reconciled")
        self.guard.require_entry_clear()
        trade = Trade.session.get(Trade, binding["trade_id"])
        if trade is None:
            raise RiskError("Zero-fill cancellation has no position to exit")
        return self.exits.dispatch(trade, checked, exchange, bot, now, limit=limit)
