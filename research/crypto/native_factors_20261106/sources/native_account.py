"""Durable account-event reconciliation into the existing native RD1 bridge.

The source must explicitly declare complete inception-to-checkpoint coverage.
This local adapter verifies arithmetic and native inventory, not the truth of
that declaration, exchange authentication, or historical bid/depth availability.
"""
import copy
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
import threading

from freqtrade.persistence import Trade
from native_snapshot import NativeSnapshotBridge, EPS
from native_lifecycle import CampaignFill
from native_evidence import decimal_text
from account_replay import AccountReplay, nanoseconds
from rd1 import (Quote, Funding, RiskError, StateError, Store, aware, dec, digest,
                 load_contract, parse, primitive, stamp)


def utc_text(value):
    """Normalize the explicit UTC suffix without rounding fractional seconds."""
    if isinstance(value, datetime):
        value = stamp(value)
    if isinstance(value, str) and value.endswith("+00:00"):
        value = value[:-6] + "Z"
    try:
        nanoseconds(value)
    except (TypeError, ValueError) as exc:
        raise RiskError("Explicit UTC event timestamp required") from exc
    return value


def event_ns(value):
    return nanoseconds(utc_text(value))


@dataclass(frozen=True)
class QuoteReceipt:
    quote: Quote
    received_at: datetime
    source_id: str
    evidence_class: str


class NativeAccountProvider:
    def __init__(self, directory, lifecycle, *, source_id, evidence_class, cost_scenario,
                 inception_at, create=False):
        self.bridge = NativeSnapshotBridge(lifecycle)
        self.risk = lifecycle.risk
        self.market = self.risk.market
        self.inception = aware(inception_at)
        if self.inception != self.inception.replace(hour=0, minute=0, second=0, microsecond=0):
            raise RiskError("Account inception requires an exact UTC midnight")
        if not source_id or evidence_class not in ("synthetic", "captured"):
            raise RiskError("Explicit account source and evidence class required")
        plan, _ = load_contract()
        scenarios = {s["id"]: s for s in plan["markets"][self.market]["cost_scenarios"]}
        if cost_scenario not in scenarios:
            raise RiskError("Unknown frozen cost scenario")
        self.exit_rate = dec(scenarios[cost_scenario]["total_bps_per_side"])/10000
        self.source_id = source_id; self.evidence_class = evidence_class
        identity = dict(self.risk.s["identity"], layer="native_account_events_v1", source_id=source_id,
            evidence_class=evidence_class, cost_scenario=cost_scenario, inception_at=stamp(self.inception))
        self.store = Store(Path(directory), identity, create=create)
        self._mutex = threading.RLock(); self._poisoned = False
        if create:
            self.s = {"schema_version": 1, "sequence": 0, "identity": identity,
                      "events": [], "checkpoints": {}, "day_anchors": {}, "last_cursor": None}
            self._persist()
        else:
            self.s = self.store.load()
        try:
            self.ledger = self._replay(self.s["events"])
        except Exception:
            self.store.close()
            raise

    def close(self):
        self.store.close()

    def _ready(self):
        if self._poisoned or self.store.lock.closed:
            raise StateError("Account provider writer unavailable")
        if not Trade.use_db or Trade.session.get_bind() is not self.bridge.engine:
            raise RiskError("Account provider native database identity changed")

    def _persist(self):
        self._ready()
        self.s["sequence"] += 1
        try:
            self.store.save(self.s)
        except Exception as exc:
            self._poisoned = True
            raise StateError("Account evidence commit not acknowledged; reopen and reconcile") from exc

    def _replay(self, events):
        ledger = AccountReplay(self.market, sequence_basis="source_ordered", evidence_class=self.evidence_class)
        fill_ids = set(); campaigns = {}; initial_orders = {}; funding_ids = {}
        for event in events:
            if event.get("source_id") != self.source_id or event.get("evidence_class") != self.evidence_class:
                raise RiskError("Account event source/class differs from registered stream")
            if event.get("account_id") != self.risk.s["identity"]["account_id"] or event.get("market") != self.market:
                raise RiskError("Account event identity mismatch")
            pair = event.get("pair"); campaign = event.get("campaign_id"); trade_id = event.get("native_trade_id")
            if pair not in self.risk.universe or not isinstance(campaign, str) or not campaign or type(trade_id) is not int or trade_id <= 0:
                raise RiskError("Account event needs a frozen pair and explicit native campaign binding")
            binding = (pair, trade_id)
            if campaign in campaigns and campaigns[campaign] != binding:
                raise RiskError("Account campaign identity was reused")
            if campaign not in campaigns and binding in campaigns.values():
                raise RiskError("Native campaign has multiple source identities")
            campaigns[campaign] = binding
            for key in ("event_at", "received_at"):
                ns = event_ns(event[key])
                if ns < event_ns(self.inception) or ns % 1000:
                    raise RiskError("Event predates inception or exceeds native microsecond precision")
            if event["kind"] == "fill":
                fid = event.get("fill_id"); oid = event.get("order_id")
                if not isinstance(fid, str) or not fid or not isinstance(oid, str) or not oid:
                    raise RiskError("Explicit source fill and venue order IDs required")
                key = (pair, oid, fid)
                if key in fill_ids:
                    raise RiskError("Source fill was repeated under another account event")
                fill_ids.add(key)
                if "fee_base" not in event or dec(event["fee_base"]) != 0:
                    raise RiskError("Base fee/dust settlement is outside this account provider")
                if dec(event["signed_quantity"]) > 0:
                    if campaign in initial_orders and initial_orders[campaign] != oid:
                        raise RiskError("Additional entry order is outside frozen initial-entry scope")
                    initial_orders[campaign] = oid
            elif event["kind"] in ("funding_accrual", "funding_settlement"):
                if campaign not in initial_orders:
                    raise RiskError("Funding has no preceding entry campaign")
                fid = event["funding_id"]
                if fid in funding_ids and funding_ids[fid] != (campaign, pair):
                    raise RiskError("Funding identity reused across campaigns")
                funding_ids[fid] = (campaign, pair)
            normalized = dict(event, event_at=utc_text(event["event_at"]), received_at=utc_text(event["received_at"]))
            ledger.apply(normalized)
            if any(pos["quantity"] < 0 for pos in ledger.positions.values()):
                raise RiskError("Native F1/F2 account scope is long-only 1x")
        return ledger

    def append(self, event):
        """Durably ingest a source event; this never mutates native Trade rows."""
        with self._mutex:
            self._ready()
            event = primitive(copy.deepcopy(event))
            existing = next((x for x in self.s["events"] if x["event_id"] == event["event_id"]), None)
            if existing is not None:
                if existing != event:
                    raise RiskError("Conflicting duplicate account event")
                return False
            if self.s["last_cursor"] is None:
                raise RiskError("Verify the inception account before ingesting financial events")
            if self.s["last_cursor"] is not None:
                previous = self.s["checkpoints"][str(self.s["last_cursor"])]["input"]["evidence"]["snapshot"]
                covered_at = max(event_ns(previous["balance_at"]), event_ns(previous["orders_at"]))
                if event_ns(event["received_at"]) <= event_ns(previous["at"]) or event_ns(event["event_at"]) <= covered_at:
                    raise RiskError("Late event contradicts an accepted complete account checkpoint")
            events = self.s["events"] + [event]
            ledger = self._replay(events)
            self.s["events"] = events
            self._persist()
            self.ledger = ledger
            return True

    def campaign_fills(self, campaign_id):
        self._ready()
        result = []
        for event in self.s["events"]:
            if event["kind"] != "fill" or event["campaign_id"] != campaign_id:
                continue
            quantity = dec(event["signed_quantity"])
            result.append(CampaignFill(event["order_id"], event["fill_id"], event["pair"],
                "buy" if quantity > 0 else "sell", str(abs(quantity)), event["price"], event["fee_base"],
                event["fee_quote"], parse(event["event_at"]), parse(event["received_at"]), len(result)+1,
                self.source_id, self.evidence_class))
        return tuple(result)

    def _native_events(self):
        """Check every actual native fill, including closed campaign history."""
        trades = Trade.get_trades_proxy()
        native = {t.id: t for t in trades}; totals = {}; campaigns = {}; funding = {}
        order_keys = [(o.ft_pair, o.order_id) for t in trades for o in t.orders]
        if len(set(order_keys)) != len(order_keys):
            raise RiskError("Ambiguous pair-scoped native order history")
        for event in self.s["events"]:
            tid = event["native_trade_id"]; campaign = event["campaign_id"]
            trade = native.get(tid)
            if trade is None or trade.pair != event["pair"] or trade.is_short or trade.leverage != 1:
                raise RiskError("Account event native campaign is missing or changed")
            campaigns[campaign] = tid
            if event["kind"] == "fill":
                order = trade.select_order_by_order_id(event["order_id"])
                q = dec(event["signed_quantity"])
                if order is None or order.ft_order_side not in (("buy",) if q > 0 else ("sell", "stoploss")):
                    raise RiskError("Account fill native order/direction mismatch")
                amount, cost = totals.get(order.id, (D(0), D(0)))
                totals[order.id] = (amount+abs(q), cost+abs(q)*dec(event["price"]))
            else:
                funding[event["funding_id"]] = (tid, dec(event["cost_quote"]))
        for trade in trades:
            for order in trade.orders:
                amount, cost = totals.get(order.id, (D(0), D(0)))
                if order.filled is None or abs(amount-dec(decimal_text(order.filled))) > EPS or order.safe_fee_base != 0:
                    raise RiskError("Native cumulative fills/base fees differ from complete account ledger")
                if amount and (order.average is None or abs(cost/amount-dec(decimal_text(order.average))) > EPS):
                    raise RiskError("Native average differs from account fills")
            if self.market == "futures":
                # Native funding sign is credit-positive; RD1 cost is debit-positive.
                cost = sum((v for tid, v in funding.values() if tid == trade.id), D(0))
                actual = D(0) if trade.funding_fees is None and cost == 0 else dec(decimal_text(trade.funding_fees))
                if abs(actual+cost) > EPS:
                    raise RiskError("Native campaign funding differs from source accrual/settlement")
        return campaigns

    def checkpoint(self, evidence, quote_receipts, now, *, complete_sequence,
                   complete_from_inception, funding_complete):
        """Return a checked AccountEvidence plus arithmetic audit, not permission.

Day anchors are formed only from accepted exact-midnight whole-account evidence.
Source position exit_cost and day_start fields are replaced by the frozen cost
calculation and the verified persisted anchor; raw inputs remain in the receipt.
"""
        with self._mutex:
            self._ready()
            if Trade.session.new or Trade.session.dirty or Trade.session.deleted:
                raise RiskError("Account checkpoint requires committed native inventory")
            evidence = copy.deepcopy(evidence); quote_receipts = tuple(quote_receipts)
            snap = evidence.snapshot; at = aware(snap.at); now = aware(now)
            if (evidence.source_id != self.source_id or evidence.evidence_class != self.evidence_class
                    or evidence.account_id != self.risk.s["identity"]["account_id"] or evidence.market != self.market):
                raise RiskError("Account checkpoint source/identity mismatch")
            if complete_from_inception is not True or funding_complete is not True:
                raise RiskError("Complete inception events and funding coverage must be explicit")
            if snap.funding_complete is not True:
                raise RiskError("Source account explicitly lacks complete funding coverage")
            if type(complete_sequence) is not int or complete_sequence != self.ledger.last_sequence:
                raise RiskError("Account completeness watermark differs from durable event sequence")
            if type(snap.cursor) is not int or snap.cursor < 0 or not self.inception <= at <= now or (now-at).total_seconds() > 15:
                raise RiskError("Account checkpoint clock or cursor invalid")
            if self.s["last_cursor"] is None and (at != self.inception or self.s["events"]):
                raise RiskError("First checkpoint must prove the flat 1000 USDT inception account")
            if self.s["last_cursor"] is None and (dec(snap.balance) != 1000 or snap.positions or snap.orders or snap.funding or Trade.get_trades_proxy()):
                raise RiskError("Inception account must be exactly 1000 USDT with no native or source inventory")
            input_value = primitive({"evidence": asdict(evidence), "quote_receipts": [asdict(q) for q in quote_receipts],
                "complete_sequence": complete_sequence, "complete_from_inception": complete_from_inception,
                "funding_complete": funding_complete})
            old = self.s["checkpoints"].get(str(snap.cursor))
            if old and old["input"] != input_value:
                raise RiskError("Conflicting same-cursor account checkpoint")
            if self.s["last_cursor"] is not None:
                prior = self.s["checkpoints"][str(self.s["last_cursor"])]["input"]["evidence"]["snapshot"]
                if snap.cursor < self.s["last_cursor"] or at < parse(prior["at"]):
                    raise RiskError("Account checkpoint regressed")
            if self.ledger.last_event_ns is not None:
                if any(event_ns(t) < self.ledger.last_event_ns for t in (snap.balance_at, snap.orders_at)):
                    raise RiskError("Balance/orders observation predates included financial events")
            quotes = {}; risk_quotes = []
            for receipt in quote_receipts:
                q = receipt.quote
                if (not receipt.source_id or receipt.evidence_class != self.evidence_class or q.pair in quotes
                        or q.pair not in self.risk.universe or not aware(q.at) <= aware(receipt.received_at) <= at
                        or (at-aware(q.at)).total_seconds() > 15):
                    raise RiskError("Quote receipt identity, class, freshness or availability invalid")
                quotes[q.pair] = dict(price=str(dec(q.price)), event_at=utc_text(q.at), received_at=utc_text(receipt.received_at), kind=q.kind)
                risk_quotes.append(q)
            if tuple(risk_quotes) != snap.quotes:
                raise RiskError("Account valuation quotes lack identical explicit receipt evidence")
            value = self.ledger.value(quotes, utc_text(at), self.exit_rate, funding_complete=True)
            expected = {p.position_id: p for p in value["positions"]}
            observed = {p.position_id: p for p in snap.positions}
            if len(observed) != len(snap.positions) or set(expected) != set(observed):
                raise RiskError("Observed account positions differ from complete financial ledger")
            positions = []
            for pid, pos in observed.items():
                want = expected[pid]
                if (pos.pair != want.pair or dec(pos.leverage) != 1
                        or abs(dec(pos.quantity)-want.quantity) > EPS or abs(dec(pos.entry_price)-want.entry_price) > EPS):
                    raise RiskError("Observed account quantity or cost basis differs from ledger")
                positions.append(replace(pos, exit_cost=want.exit_cost))
            funding = tuple(Funding(k, v, False) for k, v in self.ledger.pending_funding.items()) + tuple(
                Funding(k, v, True) for k, v in self.ledger.settled_funding.items())
            if any(type(f.settled_in_wallet) is not bool for f in snap.funding):
                raise RiskError("Source funding settlement status is ambiguous")
            if {f.event_id: (dec(f.cost), f.settled_in_wallet) for f in snap.funding} != {f.event_id: (f.cost, f.settled_in_wallet) for f in funding} or len(snap.funding) != len(funding):
                raise RiskError("Observed account funding differs from complete event ledger")
            difference = dec(snap.balance)-value["wallet"]
            if abs(difference) > dec(self.risk.r["network"]["equity_reconciliation_tolerance_usdt"]):
                raise RiskError("Observed wallet differs from complete cash/fee/funding ledger")
            midnight = at.replace(hour=0, minute=0, second=0, microsecond=0); day = midnight.date().isoformat()
            observed_equity = value["equity"]+difference
            if at == midnight:
                anchor = str(observed_equity)
                if day in self.s["day_anchors"] and self.s["day_anchors"][day]["equity"] != anchor:
                    raise RiskError("Previously verified UTC day anchor changed")
            elif day in self.s["day_anchors"]:
                anchor = self.s["day_anchors"][day]["equity"]
            else:
                raise RiskError("Exact UTC midnight account evidence is missing")
            computed = replace(snap, positions=tuple(positions), funding=funding, funding_complete=True,
                day_start_at=midnight, day_start_equity=dec(anchor), reconciliation_passed=True,
                reconciliation_difference=difference)
            result = replace(evidence, snapshot=computed)
            self.bridge.validate(result, now)
            campaigns = self._native_events()
            if any(campaigns.get(pid) != tid for pid, tid in evidence.position_bindings.items()):
                raise RiskError("Source/native campaign bindings differ from financial events")
            if abs(self.risk.equity(computed)-observed_equity) > EPS:
                raise RiskError("RD1 equity and full account replay disagree")
            audit = primitive(dict(ledger_equity=value["equity"], observed_equity=observed_equity,
                wallet=value["wallet"], observed_wallet=snap.balance, reconciliation_difference=difference,
                fees_paid=value["fees_paid"], realized_price_pnl=value["realized_price_pnl"],
                pending_funding_cost=value["pending_funding_cost"], settled_funding_cash=value["settled_funding_cash"],
                exit_cost_reserve=sum((p.exit_cost for p in positions), D(0)), event_sequence=complete_sequence,
                source_completeness_certified_by_this_code=False, economic_qualified=False))
            receipt = {"input": input_value, "output": primitive(asdict(result)), "audit": audit}
            if old and old != receipt:
                raise RiskError("Replayed account checkpoint changed its result")
            if not old:
                self.s["checkpoints"][str(snap.cursor)] = receipt; self.s["last_cursor"] = snap.cursor
                if at == midnight:
                    self.s["day_anchors"][day] = {"equity": anchor, "checkpoint_cursor": snap.cursor, "input_hash": digest(input_value)}
                self._persist()
            return result, audit
