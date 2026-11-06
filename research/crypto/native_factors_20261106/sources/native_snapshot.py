"""Validate explicit account evidence against an isolated native database.

No balance, quote timestamp, funding settlement flag, historical rule or wire
receipt is synthesized here. Source completeness remains an external claim.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import sys

from freqtrade.constants import MATH_CLOSE_PREC
from freqtrade.persistence import Trade

OLD = Path(__file__).resolve().parents[2] / "rd_program_20261105"
sys.path.insert(0, str(OLD / "execution_extension"))
from native_evidence import CapturedOrder, decimal_text
from rd1 import Snapshot, RiskError, aware, dec

EPS = D(str(MATH_CLOSE_PREC))


@dataclass(frozen=True)
class AccountEvidence:
    account_id: str
    market: str
    source_id: str
    evidence_class: str
    snapshot: Snapshot
    positions_complete: bool
    orders_complete: bool
    balance_complete: bool
    # Source position/campaign ID -> native Trade ID, never inferred from pair alone.
    position_bindings: dict[str, int]
    # RD1 order ID -> (pair, venue order ID); venue IDs need not be globally unique.
    order_bindings: dict[str, tuple[str, str]]
    order_envelopes: tuple[CapturedOrder, ...]
    partial_base_fees: dict[str, str]


class NativeSnapshotBridge:
    def __init__(self, reservations):
        self.reservations = reservations
        self.account_id = reservations.risk.s["identity"]["account_id"]
        self.market = reservations.risk.market
        self.engine = Trade.session.get_bind()

    def validate(self, evidence, now):
        if not Trade.use_db or Trade.session.get_bind() is not self.engine:
            raise RiskError("Native database identity changed or unavailable")
        if Trade.session.new or Trade.session.dirty or Trade.session.deleted:
            raise RiskError("Native account has uncommitted changes")
        if (evidence.account_id, evidence.market) != (self.account_id, self.market):
            raise RiskError("Account evidence identity mismatch")
        if not evidence.source_id or evidence.evidence_class not in ("synthetic", "captured"):
            raise RiskError("Source identity and evidence class required")
        if any(flag is not True for flag in (evidence.positions_complete, evidence.orders_complete, evidence.balance_complete)):
            raise RiskError("Incomplete source account inventory")
        snap = evidence.snapshot
        now = aware(now)
        if not 0 <= (now-aware(snap.at)).total_seconds() <= 15:
            raise RiskError("Snapshot is stale or future")
        # Get the native inventory ourselves. A caller cannot silently omit a Trade.
        trades = Trade.get_trades_proxy()
        if any(t.exchange != "binance" or t.trading_mode.value != self.market for t in trades):
            raise RiskError("Native database contains a different account market")
        active = [t for t in trades if t.is_open or any(o.ft_is_open for o in t.orders)]
        native_orders = [o for t in active for o in t.orders if o.ft_is_open]
        order_map = {(o.ft_pair, o.order_id): o for o in native_orders}
        source_ids = {o.order_id: o for o in snap.orders}
        if len(order_map) != len(native_orders) or len(source_ids) != len(snap.orders):
            raise RiskError("Duplicate open order identity")
        if set(source_ids) != set(evidence.order_bindings):
            raise RiskError("Source order binding coverage differs")
        reverse = {tuple(native): source for source, native in evidence.order_bindings.items()}
        if len(reverse) != len(evidence.order_bindings) or set(reverse) != set(order_map):
            raise RiskError("Source/native open order inventories differ")
        source_orders = {key: source_ids[oid] for key, oid in reverse.items()}
        reports = {}
        raws = {}
        for envelope in evidence.order_envelopes:
            if envelope.evidence_class != evidence.evidence_class:
                raise RiskError("Mixed evidence classes")
            raw = json.loads(envelope.payload_json)
            oid = (raw.get("symbol"), raw.get("id"))
            if oid not in order_map or oid in reports:
                raise RiskError("Unknown or duplicate captured order")
            age = (now-aware(envelope.received_at)).total_seconds()
            if not 0 <= age <= 15 or aware(envelope.received_at) > aware(snap.at):
                raise RiskError("Order receipt is stale/future or after snapshot")
            reports[oid] = envelope.normalize(order_map[oid])
            raws[oid] = raw
            if type(raw.get("timestamp")) is not int:
                raise RiskError("Original order creation timestamp required")
            created = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=raw["timestamp"])
            if aware(source_orders[oid].created_at) != created or created > aware(envelope.received_at):
                raise RiskError("Order creation/receipt chronology mismatch")
        if set(order_map) != set(source_orders) or set(reports) != set(order_map):
            raise RiskError("Source/native open order inventories differ")
        native_positions = {}
        reductions = {}
        used_fee_ids = set()
        for trade in active:
            if trade.is_short or trade.leverage != 1:
                raise RiskError("Only frozen 1x long scope supported")
            quantity = dec(decimal_text(trade.amount)) if trade.is_open else D(0)
            entry = dec(decimal_text(trade.open_rate))
            partials = []
            for order in trade.orders:
                if not order.ft_is_open:
                    continue
                key = (order.ft_pair, order.order_id)
                source_id = reverse[key]
                report = reports[key]
                source = source_orders[key]
                entry_order = order.ft_order_side == trade.entry_side
                signed = D(1) if entry_order else D(-1)
                if report["status"] != "open" or report["side"] != ("buy" if entry_order else "sell"):
                    raise RiskError("Native open order status/direction inconsistent")
                if dec(report["amount"]) <= 0 or dec(report["filled"]) < 0 or abs(dec(report["amount"])-dec(report["filled"])-dec(report["remaining"])) > EPS:
                    raise RiskError("Open order amount decomposition inconsistent")
                if source.pair != trade.pair or source.increases_risk != entry_order or source.reduce_only == entry_order:
                    raise RiskError("Order direction/risk classification mismatch")
                if self.market == "futures" and not entry_order and raws[key].get("reduceOnly") is not True:
                    raise RiskError("Futures protective order lacks captured reduceOnly")
                remaining = dec(report["remaining"])
                if remaining <= 0 or dec(source.remaining_quantity) != signed*remaining:
                    raise RiskError("Open order remaining quantity mismatch or awaiting terminal state")
                if dec(source.limit_price) != dec(report["price"]):
                    raise RiskError("Open order limit price mismatch")
                filled = dec(report["filled"])
                if filled:
                    if source_id not in evidence.partial_base_fees:
                        raise RiskError("Partial-fill base fee is unknown")
                    fee = dec(evidence.partial_base_fees[source_id])
                    used_fee_ids.add(source_id)
                    if fee < 0 or fee >= filled or abs(fee-dec(decimal_text(order.safe_fee_base))) > EPS:
                        raise RiskError("Partial-fill base fee differs from native evidence")
                    if entry_order:
                        average = raws[key].get("average")
                        if average is None or order.average is None or dec(decimal_text(average)) <= 0:
                            raise RiskError("Partial entry requires actual average fill price")
                        if dec(decimal_text(average)) != dec(decimal_text(order.average)):
                            raise RiskError("Partial average fill price mismatch")
                        partials.append((signed, filled-fee, dec(decimal_text(average))))
                    else:
                        partials.append((signed, filled-fee, entry))
                if not entry_order:
                    reductions[trade.id] = reductions.get(trade.id, D(0)) + remaining
            if len({sign for sign, _, _ in partials}) > 1:
                raise RiskError("Mixed partial entry/exit order requires complete fill ordering")
            for sign, filled, price in partials:
                if sign > 0:
                    entry = (quantity*entry + filled*price)/(quantity+filled)
                quantity += sign*filled
            if quantity < 0 or (not trade.is_open and quantity != 0):
                raise RiskError("Native partial inventory inconsistent with closed state")
            if reductions.get(trade.id, D(0)) > quantity:
                raise RiskError("Protective exit remainder exceeds held quantity")
            if quantity:
                native_positions[trade.id] = (trade.pair, quantity, entry, dec(decimal_text(trade.leverage)))
        if used_fee_ids != set(evidence.partial_base_fees):
            raise RiskError("Unmatched partial fee evidence")
        positions = {p.position_id: p for p in snap.positions}
        if len(positions) != len(snap.positions) or set(positions) != set(evidence.position_bindings):
            raise RiskError("Source position binding coverage differs")
        if len(set(evidence.position_bindings.values())) != len(evidence.position_bindings) or set(evidence.position_bindings.values()) != set(native_positions):
            raise RiskError("Source/native position inventories differ")
        for pid, native_id in evidence.position_bindings.items():
            pair, quantity, entry, leverage = native_positions[native_id]
            pos = positions[pid]
            if pos.pair != pair or abs(dec(pos.quantity)-quantity) > EPS or abs(dec(pos.entry_price)-entry) > EPS or dec(pos.leverage) != leverage:
                raise RiskError("Source/native position values differ")
        # Existing RD1 remains the controlling balance/funding/quote/clock contract.
        self.reservations.risk.equity(snap)
        quality = self.reservations.risk.quality(snap, now)
        if quality:
            raise RiskError("RD1 snapshot quality: " + ",".join(quality))
        return snap

    def reserve(self, request_id, evidence, proposal, now):
        snap = self.validate(evidence, now)
        # This is still the old durable intent path; never exchange authorization.
        return self.reservations.reserve(request_id, snap, proposal, aware(now))
