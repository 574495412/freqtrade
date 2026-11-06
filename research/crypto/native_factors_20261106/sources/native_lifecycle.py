"""Reconcile native entry reservations with explicit complete campaign evidence.

Uses the frozen RD1 and reservation persistence; never sends or changes orders.
Source completeness is an explicit input, not independently authenticated here.
"""
import copy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import json

from freqtrade.persistence import Order, Trade
from native_snapshot import NativeSnapshotBridge, EPS
from native_evidence import decimal_text
from lifecycle import Lifecycle
from rd1 import RiskError, StateError, aware, dec, digest, parse, primitive, stamp


@dataclass(frozen=True)
class CampaignFill:
    order_id: str
    fill_id: str
    pair: str
    side: str
    quantity: str
    price: str
    # Actual base and quote charges, not a quote equivalent of the base charge.
    fee_base: str
    fee_quote: str
    at: datetime
    received_at: datetime
    sequence: int
    source_id: str
    evidence_class: str


def scoped_order_id(pair, venue_id):
    return json.dumps([pair, venue_id], separators=(",", ":"))


class NativeLifecycle(Lifecycle):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.native_bridge = NativeSnapshotBridge(self)

    def _validate(self):
        super()._validate()
        for item in self.s["requests"].values():
            life = item.get("lifecycle", {})
            if life.get("state") in ("OBSERVED_OPEN", "TERMINAL_RECONCILED"):
                if life.get("native_reconciliation_version") != 1:
                    raise StateError("Legacy reconciliation is not native inventory proof")
                if life["order_id"] != scoped_order_id(item["proposal"]["pair"], life["venue_order_id"]):
                    raise StateError("Native pair-scoped order identity changed")

    def reconcile(self, *args, **kwargs):
        raise RiskError("Use reconcile_native with complete native account and fill evidence")

    def _campaign(self, fills, trade, target, pair, claimed_at, snapshot_at, evidence_class):
        seen = {}; per_order = {}; quantity = D(0); cash = D(0)
        fee_base = D(0); fee_quote = D(0); previous_at = None; previous_received = None
        sequence = 0
        native = {} if trade is None else {o.order_id: o for o in trade.orders}
        if trade is not None and len(native) != len(trade.orders):
            raise RiskError("Duplicate native campaign order identity")
        for fill in fills:
            record = primitive(asdict(fill))
            identity = scoped_order_id(fill.order_id, fill.fill_id)
            if not all(isinstance(v, str) and v for v in (fill.order_id, fill.fill_id, fill.source_id)):
                raise RiskError("Missing campaign fill identity/source")
            if identity in seen:
                if seen[identity] != record:
                    raise RiskError("Conflicting duplicate campaign fill")
                continue
            if fill.pair != pair or fill.evidence_class != evidence_class or fill.order_id not in native:
                raise RiskError("Fill is outside the native campaign/source class")
            if type(fill.sequence) is not int or fill.sequence != sequence + 1:
                raise RiskError("Incomplete declared campaign sequence")
            at, received = aware(fill.at), aware(fill.received_at)
            if not claimed_at <= at <= received <= snapshot_at:
                raise RiskError("Campaign fill chronology inconsistent")
            if (previous_at is not None and at < previous_at) or (previous_received is not None and received < previous_received):
                raise RiskError("Campaign fill order regressed; do not sort to repair")
            order = native[fill.order_id]
            entering = fill.order_id == target
            if (fill.side != ("buy" if entering else "sell")
                    or order.ft_order_side not in (("buy",) if entering else ("sell", "stoploss"))):
                raise RiskError("Additional entry or inconsistent campaign direction")
            q, price, base, quote = (dec(getattr(fill, k)) for k in ("quantity", "price", "fee_base", "fee_quote"))
            if q <= 0 or price <= 0 or base < 0 or quote < 0 or base >= q:
                raise RiskError("Invalid campaign fill quantity/price/fees")
            if base and (self.risk.market == "futures" or not entering):
                raise RiskError("Base fee requires an unsupported settlement/dust inventory contract")
            quantity += q-base if entering else -q
            if quantity < 0:
                raise RiskError("Campaign exit exceeds available net entry inventory")
            cash += (-q*price if entering else q*price)-quote
            fee_base += base; fee_quote += quote
            totals = per_order.setdefault(fill.order_id, {"gross": D(0), "base_fee": D(0), "cost": D(0)})
            totals["gross"] += q; totals["base_fee"] += base; totals["cost"] += q*price
            sequence = fill.sequence; previous_at = at; previous_received = received
            seen[identity] = record
        for oid, order in native.items():
            totals = per_order.get(oid, {"gross": D(0), "base_fee": D(0), "cost": D(0)})
            if order.filled is None or abs(totals["gross"]-dec(decimal_text(order.filled))) > EPS:
                raise RiskError("Complete fill ledger differs from native cumulative fills")
            if abs(totals["base_fee"]-dec(decimal_text(order.safe_fee_base))) > EPS:
                raise RiskError("Native applied base fee differs; dust allocation needs explicit reconciliation")
            if totals["gross"]:
                if order.average is None or abs(totals["cost"]/totals["gross"]-dec(decimal_text(order.average))) > EPS:
                    raise RiskError("Fill prices differ from actual native average")
        return {
            "entry_gross_filled": str(per_order.get(target, {}).get("gross", D(0))),
            "net_remaining_quantity": str(quantity), "base_fees": str(fee_base), "quote_fees": str(fee_quote),
            "spot_trade_cash_delta": str(cash) if self.risk.market == "spot" else None,
            "fills": seen, "full_account_NAV_verified": False, "funding_included": False}

    def reconcile_native(self, request_id, event_id, envelope, evidence, now, *, fills, fills_complete, sequence_basis):
        fills = tuple(fills)
        evidence = copy.deepcopy(evidence)
        with self._mutex:
            if self._closed or self._poisoned:
                raise StateError("Lifecycle writer unavailable")
            if not isinstance(event_id, str) or not event_id:
                raise RiskError("Reconciliation event identity required")
            item = self.s["requests"][request_id]
            life = item.get("lifecycle")
            if life is None:
                raise StateError("No claimed native attempt")
            signature = digest({"envelope": asdict(envelope), "account": asdict(evidence),
                                "fills": [asdict(f) for f in fills], "complete": fills_complete, "sequence_basis": sequence_basis})
            prior = life["events"].get(event_id)
            if prior is not None:
                if prior != signature:
                    raise StateError("Conflicting duplicate reconciliation evidence")
                return copy.deepcopy(life)  # no new authorization or freshness claim
            if life["state"] == "TERMINAL_RECONCILED":
                raise StateError("Terminal reconciliation already finalized")
            if fills_complete is not True or sequence_basis != "source_ordered":
                raise RiskError("Complete explicitly ordered campaign evidence required")
            if envelope.evidence_class != evidence.evidence_class:
                raise RiskError("Mixed order/account evidence classes")
            snap = self.native_bridge.validate(evidence, now)
            report = envelope.normalize()
            p = item["proposal"]; venue_id = report["id"]; key = scoped_order_id(p["pair"], venue_id)
            if (report["clientOrderId"] != life["client_order_id"] or report["symbol"] != p["pair"]
                    or report["side"] != "buy" or dec(p["quantity"]) <= 0
                    or life["order_id"] not in (None, key)):
                raise RiskError("Native report identity differs from claimed request")
            if any(k != request_id and x.get("lifecycle", {}).get("order_id") == key for k, x in self.s["requests"].items()):
                raise RiskError("Pair/order already bound to another request")
            status = report["status"]; filled = dec(report["filled"]); remaining = dec(report["remaining"])
            if (status not in ("open", "closed", "canceled", "expired", "rejected") or filled < dec(life["filled"])
                    or remaining < 0 or dec(report["amount"]) != dec(p["quantity"])
                    or filled+remaining != dec(p["quantity"]) or dec(report["price"]) != dec(p["price"])):
                raise RiskError("Native report status or cumulative quantities inconsistent")
            at = aware(envelope.received_at); claimed = parse(life["claimed_at"])
            if (at < claimed or at > aware(now) or (life["last_report_at"] and at < parse(life["last_report_at"]))
                    or snap.cursor <= life["claim_cursor"] or any(aware(t) < at for t in (snap.at, snap.orders_at, snap.balance_at))):
                raise RiskError("Account/report chronology does not follow claimed attempt")
            raw = json.loads(envelope.payload_json)
            if type(raw.get("timestamp")) is not int:
                raise RiskError("Original order creation time required")
            created = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=raw["timestamp"])
            if not claimed <= created <= at:
                raise RiskError("Order creation time outside claimed attempt")
            rows = Trade.session.query(Order).filter(Order.ft_pair == p["pair"], Order.order_id == venue_id).all()
            trade = None
            if len(rows) == 1:
                order = rows[0]; envelope.normalize(order)
                trade = Trade.session.get(Trade, order.ft_trade_id)
                if trade.pair != p["pair"] or trade.is_short or trade.leverage != 1 or order.ft_order_side != "buy":
                    raise RiskError("Native trade binding differs from entry contract")
                if order.ft_is_open != (status == "open"):
                    raise RiskError("Native terminal/open status differs")
            elif rows or status not in ("rejected", "expired", "canceled") or filled != 0:
                raise RiskError("Native order missing or ambiguous; keep reserved")
            campaign = self._campaign(fills, trade, venue_id, p["pair"], claimed, aware(snap.at), evidence.evidence_class)
            if dec(campaign["entry_gross_filled"]) != filled:
                raise RiskError("Campaign entry fills differ from order report")
            old_fills = life.get("native_campaign", {}).get("fills", {})
            if any(campaign["fills"].get(k) != v for k, v in old_fills.items()):
                raise RiskError("Previously reconciled fills were omitted or changed")
            actual = sum((dec(pos.quantity) for pos in snap.positions if pos.pair == p["pair"]), D(0))
            if abs(actual-dec(campaign["net_remaining_quantity"])) > EPS:
                raise RiskError("Net campaign inventory differs from complete account")
            if trade is not None:
                bound = [evidence.position_bindings[pos.position_id] for pos in snap.positions if pos.pair == p["pair"]]
                if bound != ([trade.id] if actual else []):
                    raise RiskError("Campaign position belongs to a different native trade")
            matches = [oid for oid, binding in evidence.order_bindings.items() if tuple(binding) == (p["pair"], venue_id)]
            if status == "open":
                if len(matches) != 1:
                    raise RiskError("Open entry missing from complete order inventory")
                paired = next(o for o in snap.orders if o.order_id == matches[0])
                if dec(paired.remaining_quantity) != remaining or not paired.increases_risk or paired.reduce_only:
                    raise RiskError("Remaining entry exposure differs")
                same = [o for o in evidence.order_envelopes if json.loads(o.payload_json).get("id") == venue_id
                        and json.loads(o.payload_json).get("symbol") == p["pair"]]
                if len(same) != 1 or same[0] != envelope:
                    raise RiskError("Account and lifecycle order envelopes differ")
            elif matches:
                raise RiskError("Terminal entry still open in account")
            self.risk.evaluate(snap, now)
            if not self.risk.s["last_usable"] or not self.risk.actions_safe(snap, now) or self.risk.quality(snap, now):
                raise RiskError("Unusable reconciliation account")
            updated = copy.deepcopy(life)
            updated.update(state="OBSERVED_OPEN" if status == "open" else "TERMINAL_RECONCILED",
                native_reconciliation_version=1, order_id=key, venue_order_id=venue_id,
                native_trade_id=None if trade is None else trade.id,
                last_report_at=stamp(at), filled=str(filled), snapshot_hash=digest(asdict(snap)), native_campaign=campaign)
            updated["events"][event_id] = signature
            updated.setdefault("native_evidence", {})[event_id] = primitive({"order": asdict(envelope), "account": asdict(evidence), "campaign": campaign})
            item["lifecycle"] = updated
            self._persist()
            return copy.deepcopy(updated)
