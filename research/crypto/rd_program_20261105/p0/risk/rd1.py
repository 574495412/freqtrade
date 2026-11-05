"""Offline RD1 risk intentions only. No exchange, RPC, network or trading integration.

Inputs are reconciled complete account snapshots, not incremental fills. All monetary
inputs use Decimal; the adapter must prove input provenance before any future use.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import itertools
import json
import os
from pathlib import Path
import tempfile
from typing import Any

UTC = timezone.utc
D = Decimal
PLAN = Path(__file__).resolve().parents[2] / "plan.json"


class RiskError(ValueError):
    pass


class StateError(RiskError):
    """A state failure is fail-closed: there is no usable entry authorization."""


def dec(value: Any) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise RiskError("use Decimal/string/integer, never binary float or bool")
    try:
        result = D(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RiskError("invalid decimal") from exc
    if not result.is_finite():
        raise RiskError("non-finite decimal")
    return result


def aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RiskError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def stamp(value: datetime) -> str:
    return aware(value).isoformat()


def parse(value: str) -> datetime:
    return aware(datetime.fromisoformat(value))


def primitive(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return stamp(value)
    if isinstance(value, dict):
        return {k: primitive(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [primitive(v) for v in value]
    return value


def canonical(value: Any) -> bytes:
    return json.dumps(primitive(value), sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def load_contract(plan_path: Path = PLAN) -> tuple[dict, str]:
    freeze = json.loads(plan_path.with_name("freeze.json").read_text())
    relative = "research/crypto/rd_program_20261105/plan.json"
    if hashlib.sha256(plan_path.read_bytes()).hexdigest() != freeze["files"][relative]:
        raise StateError("frozen plan digest mismatch")
    plan = json.loads(plan_path.read_text(), parse_float=Decimal)
    if plan["risk_contract"]["id"] != "RD1" or plan["runtime_changes_allowed"]:
        raise StateError("unsupported risk contract")
    return plan, digest(plan["risk_contract"])


@dataclass(frozen=True)
class Quote:
    pair: str
    price: Decimal  # spot executable conservative bid; futures mark, not last trade
    at: datetime
    kind: str  # spot_bid / futures_mark


@dataclass(frozen=True)
class Position:
    position_id: str  # unique over an account's entire lifecycle
    pair: str
    quantity: Decimal  # signed base quantity
    entry_price: Decimal
    exit_cost: Decimal  # conservative remaining exit fees/slippage, not already paid
    leverage: Decimal = D(1)
    collateral: Decimal = D(0)  # audit-only; NEVER added to or subtracted from equity


@dataclass(frozen=True)
class Funding:
    event_id: str
    cost: Decimal  # signed: payment positive, credit negative
    settled_in_wallet: bool  # settled events excluded from unpaid accrual exactly once


@dataclass(frozen=True)
class Order:
    order_id: str
    pair: str
    remaining_quantity: Decimal  # signed; filled quantities belong only in positions
    limit_price: Decimal
    created_at: datetime
    increases_risk: bool
    reduce_only: bool = False


@dataclass(frozen=True)
class Correlations:
    at: datetime
    lookback_hours: int
    edges: tuple[tuple[str, str, Decimal], ...]  # complete frozen universe pair matrix


@dataclass(frozen=True)
class Snapshot:
    cursor: int
    at: datetime
    balance_at: datetime
    orders_at: datetime
    balance: Decimal
    balance_semantics: str
    positions: tuple[Position, ...]
    orders: tuple[Order, ...]
    quotes: tuple[Quote, ...]
    funding: tuple[Funding, ...]
    funding_complete: bool
    day_start_equity: Decimal
    day_start_at: datetime  # exact UTC midnight; never infer from first later sample
    reconciliation_passed: bool
    reconciliation_difference: Decimal
    unknown_orders: bool = False
    exchange_clock_skew_seconds: Decimal = D(0)
    consecutive_api_errors: int = 0
    transport_available: bool = True
    external_capital_flow: Decimal = D(0)  # inception to now; no silent top-up/reset
    correlations: Correlations | None = None


@dataclass(frozen=True)
class Entry:
    pair: str
    quantity: Decimal
    price: Decimal
    leverage: Decimal
    stop_distance: Decimal
    spread_bps: Decimal
    previous_hour_base_volume: Decimal
    visible_contra_depth_within_20bps: Decimal
    liquidity_at: datetime
    amount_step: Decimal
    minimum_notional: Decimal
    positive_adjustment: bool = False


@dataclass(frozen=True)
class Decision:
    state: str
    equity: str | None
    reasons: tuple[str, ...]
    entry_allowed: bool
    actions: tuple[dict, ...]
    exposure: dict


class Store:
    """Single writer + checksum + fsync/replace + recoverable write-ahead state.

    A pre-existing identity prevents accidentally initializing a missing ledger.
    Corrupt state/journal always raises. A valid higher-sequence journal is replayed.
    """
    def __init__(self, directory: Path, identity: dict, create: bool = False):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = (self.directory / "writer.lock").open("a+b")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.lock.close()
            raise StateError("another writer owns this isolated account") from exc
        try:
            self.identity = identity
            path = self.directory / "identity.json"
            if create:
                if path.exists() or (self.directory / "state.json").exists():
                    raise StateError("refusing to reinitialize existing account")
                self.atomic_write(path, canonical(identity))
            elif not path.exists() or json.loads(path.read_text()) != identity:
                raise StateError("missing or mismatched account identity")
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if not self.lock.closed:
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            self.lock.close()

    def atomic_write(self, target: Path, payload: bytes) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".rd1-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            self.sync_directory()
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def sync_directory(self) -> None:
        fd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_envelope(self, path: Path) -> dict:
        try:
            envelope = json.loads(path.read_text())
            state = envelope["state"]
            if digest(state) != envelope["sha256"] or state["identity"] != self.identity:
                raise StateError("state checksum/identity mismatch")
            if state["schema_version"] != 1 or type(state["sequence"]) is not int:
                raise StateError("unsupported state schema")
            return state
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise StateError(f"unreadable state: {path.name}") from exc

    def load(self) -> dict:
        state_path, journal_path = self.directory / "state.json", self.directory / "transaction.json"
        state = self.read_envelope(state_path) if state_path.exists() else None
        if journal_path.exists():
            journal = self.read_envelope(journal_path)
            if state and journal["sequence"] < state["sequence"]:
                raise StateError("journal sequence regression")
            if state and journal["sequence"] == state["sequence"] and journal != state:
                raise StateError("same-sequence journal conflict")
            self.atomic_write(state_path, canonical({"state": journal, "sha256": digest(journal)}))
            journal_path.unlink()
            self.sync_directory()
            state = journal
        if state is None:
            raise StateError("missing persisted account; never initialize implicitly")
        return state

    def save(self, state: dict) -> None:
        envelope = canonical({"state": state, "sha256": digest(state)})
        self.atomic_write(self.directory / "transaction.json", envelope)
        self.atomic_write(self.directory / "state.json", envelope)
        (self.directory / "transaction.json").unlink()
        self.sync_directory()


class RD1:
    def __init__(self, directory: Path, account_id: str, market: str, *,
                 cost_scenario: str = "base", create: bool = False, plan_path: Path = PLAN):
        self.plan, self.contract_hash = load_contract(plan_path)
        if market not in ("spot", "futures") or not account_id:
            raise StateError("invalid isolated account identity")
        self.market = market
        self.r = self.plan["risk_contract"]
        self.universe = set(self.plan["markets"][market]["pairs"])
        scenarios = self.plan["markets"][market]["cost_scenarios"]
        self.cost = next((dec(x["total_bps_per_side"]) / 10000 for x in scenarios
                          if x["id"] == cost_scenario), None)
        if self.cost is None:
            raise StateError("unknown frozen cost scenario")
        identity = {"account_id": account_id, "market": market, "contract_hash": self.contract_hash,
                    "cost_scenario": cost_scenario}
        self.store = Store(directory, identity, create)
        self.poisoned = False
        self.boot_reconciled = False
        self.boot_actions_reconciled = False
        try:
            if create:
                self.s = {"schema_version": 1, "sequence": 0, "identity": identity,
                          "initial_equity": "1000", "high_water_mark": "1000",
                          "utc_day_start_equity": None, "day_id": None, "latches": {},
                          "pending_actions": {}, "event_cursor": -1, "snapshot_hash": None,
                          "fresh_since": None, "last_fresh_at": None,
                          "last_reconciled_cursor": -1, "last_equity": None,
                          "last_dd": None, "last_flat": False, "last_entry_orders": True,
                          "last_at": None, "last_data_at": None, "last_action_at": None,
                          "last_usable": False, "last_actions_safe": False, "last_transport": False,
                          "exposure_episode": None,
                          "state": "ENTRY_BLOCKED", "recovery_reviews": [], "recovery_after_cursor": -1}
                self.persist()
            else:
                self.s = self.store.load()
                self.validate_state()
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> RD1:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def validate_state(self) -> None:
        try:
            if dec(self.s["initial_equity"]) != 1000 or dec(self.s["high_water_mark"]) < 1000:
                raise StateError("capital or high-water invariant violated")
            if not isinstance(self.s["latches"], dict) or not isinstance(self.s["pending_actions"], dict):
                raise StateError("invalid persisted maps")
            if self.s["state"] not in self.r["state_machine"] or type(self.s["event_cursor"]) is not int:
                raise StateError("invalid persisted state/cursor")
            for key in ("last_equity", "last_dd", "utc_day_start_equity"):
                if self.s[key] is not None:
                    dec(self.s[key])
            for key in ("fresh_since", "last_fresh_at", "last_at", "last_data_at", "last_action_at"):
                if self.s[key] is not None:
                    parse(self.s[key])
            for action in self.s["pending_actions"].values():
                if action["kind"] not in ("CANCEL_ENTRY", "EXIT_POSITION", "REDUCE_EXPOSURE"):
                    raise StateError("invalid persisted action")
                if type(action["attempts"]) is not int or action["attempts"] < 0:
                    raise StateError("invalid attempt count")
        except (KeyError, TypeError, ValueError) as exc:
            raise StateError("invalid persisted state schema") from exc

    def persist(self) -> None:
        if self.poisoned:
            raise StateError("prior persistence failure: reopen and reconcile")
        self.s["sequence"] += 1
        try:
            self.store.save(self.s)
        except Exception as exc:
            self.poisoned = True
            raise StateError("durable commit failed; no authorization returned") from exc

    def equity(self, snap: Snapshot) -> Decimal:
        quotes = {q.pair: q for q in snap.quotes}
        if len(quotes) != len(snap.quotes):
            raise RiskError("duplicate quote")
        if len({p.position_id for p in snap.positions}) != len(snap.positions):
            raise RiskError("duplicate position")
        if len({o.order_id for o in snap.orders}) != len(snap.orders):
            raise RiskError("duplicate order")
        if len({f.event_id for f in snap.funding}) != len(snap.funding):
            raise RiskError("duplicate funding event")
        semantic = ("total_cash_including_reserved" if self.market == "spot" else
                    "wallet_excluding_upnl_including_collateral_and_settled_flows")
        if snap.balance_semantics != semantic:
            raise RiskError("ambiguous balance semantics")
        for q in snap.quotes:
            expected_kind = "spot_bid" if self.market == "spot" else "futures_mark"
            if q.pair not in self.universe or dec(q.price) <= 0 or q.kind != expected_kind:
                raise RiskError("invalid quote identity/price/kind")
        result = dec(snap.balance)
        if self.market == "futures" and not snap.funding_complete:
            raise RiskError("funding incomplete")
        if self.market == "spot" and snap.funding:
            raise RiskError("spot funding not applicable")
        for p in snap.positions:
            if p.pair not in self.universe or not p.position_id or dec(p.quantity) == 0:
                raise RiskError("invalid position identity/quantity")
            q = quotes[p.pair]
            if dec(q.price) <= 0 or dec(p.entry_price) <= 0 or dec(p.exit_cost) < 0:
                raise RiskError("invalid valuation inputs")
            if dec(p.collateral) < 0 or dec(p.leverage) <= 0:
                raise RiskError("invalid collateral/leverage")
            expected_kind = "spot_bid" if self.market == "spot" else "futures_mark"
            if q.kind != expected_kind:
                raise RiskError("wrong price kind")
            if self.market == "spot":
                if dec(p.quantity) < 0:
                    raise RiskError("spot short position")
                result += dec(p.quantity) * dec(q.price)
            else:
                result += dec(p.quantity) * (dec(q.price) - dec(p.entry_price))
            result -= dec(p.exit_cost)
        for funding in snap.funding:
            if type(funding.settled_in_wallet) is not bool:
                raise RiskError("ambiguous funding settlement status")
            if not funding.settled_in_wallet:
                result -= dec(funding.cost)
            else:
                dec(funding.cost)  # still reject NaN on settled audit evidence
        for order in snap.orders:
            if (not order.order_id or order.pair not in self.universe or
                    type(order.increases_risk) is not bool or type(order.reduce_only) is not bool or
                    dec(order.remaining_quantity) == 0 or dec(order.limit_price) <= 0):
                raise RiskError("invalid order state")
            aware(order.created_at)
            if order.increases_risk and order.reduce_only:
                raise RiskError("conflicting order risk classification")
            if not order.increases_risk and not order.reduce_only:
                raise RiskError("unproven protective order")
            if order.increases_risk and order.pair not in quotes:
                raise RiskError("unvalued pending entry")
        return result

    def quality(self, snap: Snapshot, now: datetime) -> list[str]:
        reasons = []
        for flag in (snap.reconciliation_passed, snap.unknown_orders, snap.funding_complete, snap.transport_available):
            if type(flag) is not bool:
                raise RiskError("ambiguous boolean account state")
        if self.s["last_at"] is not None and now < parse(self.s["last_at"]):
            reasons.append("local_clock_regression")
        if self.s["last_fresh_at"] is not None and aware(snap.at) < parse(self.s["last_fresh_at"]):
            reasons.append("snapshot_time_regression")
        timestamps = [snap.at, snap.balance_at, snap.orders_at] + [q.at for q in snap.quotes]
        for at in timestamps:
            delta = (now - aware(at)).total_seconds()
            if delta > self.r["equity"]["stale_after_seconds"]:
                reasons.append("stale_data")
            if delta < -self.r["network"]["max_clock_skew_seconds"]:
                reasons.append("future_data")
        if max(aware(t) for t in timestamps) - min(aware(t) for t in timestamps) > timedelta(seconds=15):
            reasons.append("unsynchronized_data")
        if abs(dec(snap.exchange_clock_skew_seconds)) > dec(self.r["network"]["max_clock_skew_seconds"]):
            reasons.append("clock_skew")
        if type(snap.consecutive_api_errors) is not int or snap.consecutive_api_errors < 0:
            reasons.append("invalid_api_error_count")
        elif snap.consecutive_api_errors >= self.r["network"]["consecutive_api_errors"]:
            reasons.append("api_errors")
        if not snap.transport_available:
            reasons.append("transport_unavailable")
        if not snap.reconciliation_passed or snap.unknown_orders:
            reasons.append("unreconciled_orders_positions")
        if abs(dec(snap.reconciliation_difference)) > dec(self.r["network"]["equity_reconciliation_tolerance_usdt"]):
            reasons.append("equity_reconciliation_mismatch")
        if dec(snap.external_capital_flow) != 0:
            reasons.append("unregistered_capital_flow")
        midnight = aware(snap.at).replace(hour=0, minute=0, second=0, microsecond=0)
        if aware(snap.day_start_at) != midnight or dec(snap.day_start_equity) <= 0:
            reasons.append("unknown_utc_day_baseline")
        if self.s["day_id"] == midnight.date().isoformat() and self.s["utc_day_start_equity"] is not None:
            if dec(snap.day_start_equity) != dec(self.s["utc_day_start_equity"]):
                reasons.append("changed_utc_day_baseline")
        return sorted(set(reasons))

    def actions_safe(self, snap: Snapshot, now: datetime) -> bool:
        """Cancellation/quantity-only exits require matched orders/positions, not a price."""
        try:
            if (snap.transport_available is not True or snap.reconciliation_passed is not True or
                    snap.unknown_orders is not False or abs(dec(snap.reconciliation_difference)) > D(".01") or
                    abs(dec(snap.exchange_clock_skew_seconds)) > 2 or
                    type(snap.consecutive_api_errors) is not int or not 0 <= snap.consecutive_api_errors < 3):
                return False
            if any(not -2 <= (now - aware(t)).total_seconds() <= 15 for t in (snap.at, snap.orders_at)):
                return False
            if self.s["last_at"] is not None and now < parse(self.s["last_at"]):
                return False
            if self.s["last_action_at"] is not None and aware(snap.at) < parse(self.s["last_action_at"]):
                return False
            if len({p.position_id for p in snap.positions}) != len(snap.positions):
                return False
            if len({o.order_id for o in snap.orders}) != len(snap.orders):
                return False
            for p in snap.positions:
                if not p.position_id or p.pair not in self.universe or dec(p.quantity) == 0:
                    return False
            for o in snap.orders:
                if (not o.order_id or o.pair not in self.universe or dec(o.remaining_quantity) == 0 or
                        type(o.increases_risk) is not bool or type(o.reduce_only) is not bool or
                        o.increases_risk == o.reduce_only):
                    return False
            return True
        except (RiskError, AttributeError, TypeError):
            return False

    def clusters(self, snap: Snapshot) -> tuple[dict[str, str], bool]:
        fallback = {p: "unknown_one_cluster" for p in self.universe}
        c = snap.correlations
        if c is None or c.lookback_hours != self.r["correlation_hours"]:
            return fallback, False
        if not (timedelta(0) <= aware(snap.at) - aware(c.at) <= timedelta(days=1)):
            return fallback, False
        edges = {}
        for a, b, value in c.edges:
            pair = tuple(sorted((a, b)))
            if a == b or a not in self.universe or b not in self.universe or pair in edges:
                return fallback, False
            if abs(dec(value)) > 1:
                return fallback, False
            edges[pair] = dec(value)
        if set(edges) != set(itertools.combinations(sorted(self.universe), 2)):
            return fallback, False
        parents = {p: p for p in self.universe}
        def root(p: str) -> str:
            while parents[p] != p:
                p = parents[p]
            return p
        for (a, b), value in edges.items():
            if value >= dec(self.r["correlation_threshold"]):
                parents[root(b)] = root(a)
        return {p: root(p) for p in self.universe}, True

    def exposure(self, snap: Snapshot, equity: Decimal, extra: Entry | None = None) -> dict:
        quotes = {q.pair: q for q in snap.quotes}
        by_pair: dict[str, Decimal] = {}
        net = D(0)
        position_ids = set()
        for p in snap.positions:
            value = dec(p.quantity) * dec(quotes[p.pair].price)
            by_pair[p.pair] = by_pair.get(p.pair, D(0)) + abs(value)
            net += value
            position_ids.add(p.position_id)
        pending_pairs = set()
        for order in snap.orders:
            if order.increases_risk:
                value = dec(order.remaining_quantity) * max(dec(order.limit_price), dec(quotes[order.pair].price))
                by_pair[order.pair] = by_pair.get(order.pair, D(0)) + abs(value)
                net += value
                if order.pair not in {p.pair for p in snap.positions}:
                    pending_pairs.add(order.pair)
        if extra:
            value = dec(extra.quantity) * dec(extra.price)
            by_pair[extra.pair] = by_pair.get(extra.pair, D(0)) + abs(value)
            net += value
            if extra.pair not in {p.pair for p in snap.positions}:
                pending_pairs.add(extra.pair)
        groups, complete = self.clusters(snap)
        by_cluster: dict[str, Decimal] = {}
        for pair, value in by_pair.items():
            cluster = groups[pair]
            by_cluster[cluster] = by_cluster.get(cluster, D(0)) + value
        gross = sum(by_pair.values(), D(0))
        breaches = []
        if gross > equity * dec(self.r["gross_exposure_cap"]):
            breaches.append("gross_exposure")
        if abs(net) > equity * dec(self.r["net_absolute_exposure_cap"]):
            breaches.append("net_exposure")
        if any(x > equity * dec(self.r["pair_exposure_cap"]) for x in by_pair.values()):
            breaches.append("pair_exposure")
        if any(x > equity * dec(self.r["correlation_cluster_cap"]) for x in by_cluster.values()):
            breaches.append("cluster_exposure")
        if len(position_ids) + len(pending_pairs) > self.r["max_open_positions"]:
            breaches.append("position_count")
        if any(dec(p.leverage) > dec(self.r["leverage_cap"]) for p in snap.positions):
            breaches.append("leverage")
        return {"gross": str(gross), "net": str(net), "by_pair": primitive(by_pair),
                "by_cluster": primitive(by_cluster), "correlation_complete": complete,
                "position_count": len(position_ids) + len(pending_pairs), "breaches": breaches}

    def latch(self, name: str, at: datetime, details: Any = None) -> None:
        if name not in self.s["latches"]:
            self.s["latches"][name] = {"at": stamp(at), "details": details}

    def action(self, kind: str, target: str, payload: dict) -> None:
        action_id = hashlib.sha256(canonical([self.s["identity"], kind, target])).hexdigest()[:24]
        actions = self.s["pending_actions"]
        if action_id not in actions or actions[action_id]["status"] == "satisfied":
            # Position/order lifecycle IDs must never be reused; reduction targets use incident timestamp.
            actions[action_id] = {"id": action_id, "kind": kind, "target": target, "attempts": 0,
                                  "last_attempt_cursor": -1, "status": "pending", "payload": payload}
        else:
            actions[action_id]["payload"] = payload

    def evaluate(self, snap: Snapshot, now: datetime) -> Decision:
        if self.poisoned:
            raise StateError("poisoned engine")
        now = aware(now)
        equity = None
        exp = {}
        event_valid = False
        try:
            event_hash = digest(asdict(snap))
            if type(snap.cursor) is not int or snap.cursor < 0:
                raise RiskError("invalid event cursor")
            if snap.cursor < self.s["event_cursor"]:
                raise RiskError("event cursor regression")
            if snap.cursor == self.s["event_cursor"] and event_hash != self.s["snapshot_hash"]:
                raise RiskError("conflicting duplicate event")
            event_valid = True
            equity = self.equity(snap)
            quality = self.quality(snap, now)
            exp = self.exposure(snap, equity)
        except (RiskError, KeyError, TypeError, AttributeError) as exc:
            quality = ["invalid_snapshot:" + str(exc)]
        usable = not quality
        action_safe = event_valid and self.actions_safe(snap, now)
        if action_safe:
            self.s["last_reconciled_cursor"] = snap.cursor
            self.s["last_action_at"] = stamp(min(aware(snap.at), aware(snap.orders_at)))
            self.boot_actions_reconciled = True
        self.s["last_actions_safe"] = action_safe
        if usable:
            self.s["last_data_at"] = stamp(min([aware(snap.at), aware(snap.balance_at), aware(snap.orders_at)] +
                                                   [aware(q.at) for q in snap.quotes]))
        at = now
        self.s["last_transport"] = bool(snap.transport_available)
        if quality:
            self.latch("technical", at, quality)
            self.s["fresh_since"] = None
        elif snap.cursor > self.s["event_cursor"]:
            previous = self.s["last_fresh_at"]
            if self.s["fresh_since"] is None or previous is None or aware(snap.at) - parse(previous) > timedelta(seconds=15):
                self.s["fresh_since"] = stamp(snap.at)
            self.s["last_fresh_at"] = stamp(snap.at)
            self.s["last_reconciled_cursor"] = snap.cursor
            self.boot_reconciled = snap.cursor > self.s["recovery_after_cursor"]
            if ("technical" in self.s["latches"] and self.s["fresh_since"] and
                    aware(snap.at) - parse(self.s["fresh_since"]) >= timedelta(minutes=5) and
                    not any(x in self.s["latches"] for x in ("absolute_floor", "hwm_hard_halt"))):
                del self.s["latches"]["technical"]
        elif usable:
            # Same snapshot may reconcile after a process restart, but cannot advance recovery time.
            self.boot_reconciled = snap.cursor > self.s["recovery_after_cursor"]
        if equity is not None:
            if equity <= dec(self.r["absolute_equity_floor_usdt"]):
                self.latch("absolute_floor", at)
            hwm = max(dec(self.s["high_water_mark"]), equity) if usable else dec(self.s["high_water_mark"])
            dd = (hwm - equity) / hwm
            if dd >= dec(self.r["hwm_halt_fraction"]):
                self.latch("hwm_hard_halt", at)
            if dd >= dec(self.r["hwm_warning_fraction"]):
                self.latch("hwm_warning", at)
            if usable:
                day = aware(snap.day_start_at).date().isoformat()
                if day != self.s["day_id"]:
                    self.s["day_id"] = day
                    self.s["utc_day_start_equity"] = str(dec(snap.day_start_equity))
                if equity <= dec(self.s["utc_day_start_equity"]) * (1 - dec(self.r["daily_loss_fraction"])):
                    self.latch("daily_loss", at, {"day_id": day})
                day_latch = self.s["latches"].get("daily_loss")
                if (day_latch and day > day_latch["details"]["day_id"] and not snap.positions and
                        not snap.orders and not any(k in self.s["latches"] for k in ("absolute_floor", "hwm_hard_halt"))):
                    del self.s["latches"]["daily_loss"]
                self.s["high_water_mark"] = str(hwm)
                self.s["last_equity"], self.s["last_dd"] = str(equity), str(dd)
        hard = any(k in self.s["latches"] for k in ("absolute_floor", "hwm_hard_halt"))
        exit_all = hard or "daily_loss" in self.s["latches"]
        warn = "hwm_warning" in self.s["latches"]
        breaches = exp.get("breaches", [])
        blocked = bool(self.s["latches"] or breaches or not usable)
        # Reconcile completion only from complete, fresh, matched account evidence.
        if action_safe:
            pos_ids = {p.position_id for p in snap.positions}
            order_ids = {o.order_id for o in snap.orders}
            for action in self.s["pending_actions"].values():
                if ((action["kind"] == "EXIT_POSITION" and action["target"] not in pos_ids) or
                        (action["kind"] == "CANCEL_ENTRY" and action["target"] not in order_ids) or
                        (action["kind"] == "REDUCE_EXPOSURE" and not warn and not breaches)):
                    action["status"] = "satisfied"
        if blocked or usable:
            for order in snap.orders:
                if order.increases_risk and (blocked or (now - aware(order.created_at)).total_seconds() >= self.r["liquidity"]["entry_timeout_seconds"]):
                    self.action("CANCEL_ENTRY", order.order_id, {"pair": order.pair,
                                "remaining_quantity": str(order.remaining_quantity)})
        if breaches and self.s["exposure_episode"] is None:
            self.s["exposure_episode"] = stamp(now)
        elif not breaches:
            self.s["exposure_episode"] = None
        if exit_all:
            for p in snap.positions:
                self.action("EXIT_POSITION", p.position_id, {"pair": p.pair,
                            "remaining_quantity": str(p.quantity), "reduce_only": True,
                            "execution_path": "future_verified_native_exit_adapter_required"})
        elif warn or breaches:
            target = self.s["latches"].get("hwm_warning", {}).get("at", self.s["exposure_episode"] or stamp(now))
            self.action("REDUCE_EXPOSURE", target, {"gross_fraction": str(self.r["hwm_warning_gross_target"] if warn else self.r["gross_exposure_cap"]),
                        "pair_fraction": str(self.r["pair_exposure_cap"]),
                        "cluster_fraction": str(self.r["correlation_cluster_cap"]),
                        "net_absolute_fraction": str(self.r["net_absolute_exposure_cap"]),
                        "equity": str(equity), "reduce_only": True,
                        "allocation": "native_adapter_must_reduce_without_opening_opposite_risk"})
        if exit_all:
            state = "HALTED" if action_safe and not snap.positions and not snap.orders else "EXIT_PENDING"
        elif quality or "technical" in self.s["latches"]:
            state = "ENTRY_BLOCKED"
        elif warn or breaches:
            state = "DE_RISK"
        else:
            state = "RUNNING"
        self.s["state"] = state
        self.s["last_usable"] = usable
        self.s["last_flat"] = usable and not snap.positions
        self.s["last_entry_orders"] = (not usable) or bool(snap.orders)
        self.s["last_at"] = stamp(now)
        if event_valid and snap.cursor >= self.s["event_cursor"]:
            self.s["event_cursor"] = snap.cursor
            self.s["snapshot_hash"] = event_hash
        self.persist()
        reasons = tuple(dict.fromkeys(list(self.s["latches"]) + quality + breaches))
        return Decision(state, str(equity) if equity is not None else None, reasons,
                        state == "RUNNING" and self.boot_reconciled, self.actions(), exp)

    def actions(self) -> tuple[dict, ...]:
        output = []
        for action in self.s["pending_actions"].values():
            if action["status"] != "pending":
                continue
            a = copy.deepcopy(action)
            a["claimable"] = (self.boot_actions_reconciled and self.s["last_actions_safe"] and
                              self.s["last_transport"] and
                              (a["kind"] != "REDUCE_EXPOSURE" or self.s["last_usable"]) and
                              a["last_attempt_cursor"] < self.s["last_reconciled_cursor"])
            output.append(a)
        return tuple(output)

    def claim_action(self, action_id: str, now: datetime) -> dict:
        """Persist a dispatch intention; does NOT execute. Retry needs a newer reconciliation."""
        if self.poisoned or self.s["last_action_at"] is None or not 0 <= (aware(now) - parse(self.s["last_action_at"])).total_seconds() <= 15:
            raise StateError("action claim needs current reconciliation")
        action = next((a for a in self.actions() if a["id"] == action_id), None)
        if not action or not action["claimable"]:
            raise StateError("action not claimable; reconcile before retry")
        stored = self.s["pending_actions"][action_id]
        stored["attempts"] += 1
        stored["last_attempt_cursor"] = self.s["last_reconciled_cursor"]
        self.persist()
        return copy.deepcopy(stored)

    def assess_entry(self, snap: Snapshot, proposal: Entry, now: datetime) -> tuple[bool, tuple[str, ...]]:
        """Both new trade and positive-adjustment gates. No implicit quantity enlargement."""
        decision = self.evaluate(snap, now)
        reasons = list(decision.reasons)
        if not decision.entry_allowed:
            return False, tuple(reasons or ["entry_blocked"])
        try:
            if proposal.pair not in self.universe or dec(proposal.quantity) == 0 or dec(proposal.price) <= 0:
                raise RiskError("invalid entry")
            if self.market == "spot" and dec(proposal.quantity) < 0:
                reasons.append("spot_short")
            if proposal.positive_adjustment or any(p.pair == proposal.pair for p in snap.positions):
                reasons.append("dca_disabled")
            if dec(proposal.leverage) <= 0 or dec(proposal.leverage) > dec(self.r["leverage_cap"]):
                reasons.append("leverage")
            if dec(proposal.stop_distance) != dec(self.r["stop_price_distance_fraction"]):
                reasons.append("unregistered_stop_distance")
            if any(o.pair == proposal.pair and o.increases_risk for o in snap.orders):
                reasons.append("pending_entry_same_pair_cancel_and_reconcile_first")
            quotes = {q.pair: q for q in snap.quotes}
            if proposal.pair not in quotes:
                raise RiskError("missing synchronized entry quote")
            notional = abs(dec(proposal.quantity)) * max(dec(proposal.price), dec(quotes[proposal.pair].price))
            equity = dec(decision.equity)
            available = dec(snap.balance)
            if self.market == "futures":
                available -= sum((dec(p.collateral) for p in snap.positions), D(0))
            for pending in snap.orders:
                if pending.increases_risk:
                    available -= abs(dec(pending.remaining_quantity)) * max(dec(pending.limit_price), dec(quotes[pending.pair].price)) * (1 + self.cost)
            if notional * (1 + self.cost) > available:
                reasons.append("insufficient_unreserved_balance")
            risk = notional * (dec(proposal.stop_distance) + 2 * self.cost)
            if risk > equity * dec(self.r["trade_risk_fraction"]):
                reasons.append("trade_risk_including_roundtrip_cost")
            if dec(proposal.amount_step) <= 0 or dec(proposal.minimum_notional) <= 0:
                raise RiskError("missing exchange quantity constraints")
            if abs(dec(proposal.quantity)) % dec(proposal.amount_step) != 0:
                reasons.append("amount_precision")
            if notional < dec(proposal.minimum_notional):
                reasons.append("minimum_notional_skip")
            liq = self.r["liquidity"]
            if (aware(now) - aware(proposal.liquidity_at)).total_seconds() > 15 or aware(proposal.liquidity_at) > aware(now) + timedelta(seconds=2):
                reasons.append("stale_liquidity")
            if dec(proposal.spread_bps) < 0 or dec(proposal.spread_bps) > dec(liq["max_spread_bps"]):
                reasons.append("spread")
            if dec(proposal.previous_hour_base_volume) <= 0 or abs(dec(proposal.quantity)) > dec(proposal.previous_hour_base_volume) * dec(liq["max_fraction_previous_hour_base_volume"]):
                reasons.append("volume_participation")
            if dec(proposal.visible_contra_depth_within_20bps) <= 0 or abs(dec(proposal.quantity)) > dec(proposal.visible_contra_depth_within_20bps) * dec(liq["max_fraction_visible_contra_depth_within_20bps"]):
                reasons.append("visible_depth")
            reasons.extend(self.exposure(snap, equity, replace(proposal, price=max(dec(proposal.price), dec(quotes[proposal.pair].price))))["breaches"])
        except (RiskError, KeyError, TypeError) as exc:
            reasons.append("invalid_entry:" + str(exc))
        return not reasons, tuple(dict.fromkeys(reasons))

    def review_recovery(self, now: datetime, *, authorization_id: str, incident_review: str,
                        clear_hard: bool = False, clear_warning: bool = False) -> None:
        """Explicit offline review. Initial capital, peaks and loss baselines never reset."""
        if not authorization_id.strip() or not incident_review.strip():
            raise StateError("explicit reauthorization and incident review required")
        if (not self.boot_reconciled or not self.s["last_usable"] or not self.s["last_flat"] or
                self.s["last_entry_orders"] or self.s["last_data_at"] is None or
                not 0 <= (aware(now) - parse(self.s["last_data_at"])).total_seconds() <= 15):
            raise StateError("recovery requires fresh reconciliation, flat account and no orders")
        equity, dd = dec(self.s["last_equity"]), dec(self.s["last_dd"])
        if clear_hard and (equity <= 500 or dd >= dec(self.r["hwm_halt_fraction"])):
            raise StateError("hard trigger still present; cannot reset capital/peak")
        if clear_warning and dd >= dec(self.r["hwm_warning_fraction"]):
            raise StateError("warning trigger still present")
        # Validate the entire review before mutating ANY latch.
        if clear_hard:
            for key in ("absolute_floor", "hwm_hard_halt"):
                self.s["latches"].pop(key, None)
        if clear_warning:
            self.s["latches"].pop("hwm_warning", None)
        self.s["recovery_reviews"].append({"at": stamp(now), "authorization_id": authorization_id,
                                          "incident_review": incident_review,
                                          "clear_hard": clear_hard, "clear_warning": clear_warning})
        self.s["state"] = "ENTRY_BLOCKED"  # a new matched snapshot is required after review
        self.boot_reconciled = False
        self.s["recovery_after_cursor"] = self.s["event_cursor"]
        self.persist()
