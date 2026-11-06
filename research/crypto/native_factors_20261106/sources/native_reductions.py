"""Durable reduction intents through native Trade metadata, no order submission.

Targets must be supplied by the still-pending shared RD1 account adapter. This
module never treats missing orders, elapsed time or restarts as proof of no fill.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import fcntl
import hashlib
import math
from pathlib import Path
import threading

from freqtrade.persistence import CustomDataWrapper, Trade
from freqtrade.persistence.custom_data import _CustomData
from freqtrade.constants import MATH_CLOSE_PREC

KEY = "rd1_reduction_v1"
EPSILON = Decimal(str(MATH_CLOSE_PREC))


def dec(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Nonfinite quantity")
    return result


def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Aware observation time required")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ReductionTarget:
    account_id: str
    trade_id: int
    pair: str
    sequence: int
    observed_at: datetime
    observed_quantity: str
    max_remaining_quantity: str
    reason: str


def scale_target(target, scale):
    quantity = dec(target.max_remaining_quantity)
    if quantity < 0:
        raise ValueError("Negative target")
    # A risk-mandated full exit must not depend on an available factor value.
    if quantity == 0:
        return target
    if not math.isfinite(scale) or not 0 < scale <= 1:
        return None
    return replace(target, max_remaining_quantity=str(quantity * dec(scale)))


class ReductionController:
    def __init__(self, directory, account_id):
        if not account_id:
            raise ValueError("Account identity required")
        self.account_id = account_id
        self.engine = Trade.session.get_bind()
        self.poisoned = False
        self.closed = False
        self.mutex = threading.RLock()
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        self.lock = (path / (hashlib.sha256(account_id.encode()).hexdigest() + ".lock")).open("a+b")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            self.lock.close()
            raise

    def close(self):
        with self.mutex:
            if not self.closed:
                self.lock.close()
                self.closed = True

    def _save(self, trade, state):
        try:
            trade.set_custom_data(KEY, state)
            if trade.get_custom_data(KEY) != state:
                raise RuntimeError("Native metadata did not persist")
        except Exception:
            self.poisoned = True
            raise

    def issue(self, trade, target, now):
        """Return native (negative stake, tag), or None. Never positive/DCA."""
        with self.mutex:
            if self.closed or self.poisoned:
                raise RuntimeError("Reduction controller unusable")
            if not CustomDataWrapper.use_db or not isinstance(trade, Trade) or trade.id is None:
                raise ValueError("Persisted native Trade and database required")
            if Trade.session.get_bind() is not self.engine or _CustomData.session.get_bind() is not self.engine:
                raise RuntimeError("Native database binding changed")
            # Native metadata and Trade use separate sessions. They are not one
            # atomic transaction: require a durable position and clean metadata
            # session before recording an intent, retain unknown results afterward.
            sessions = (Trade.session, _CustomData.session)
            if any(s.new or s.dirty or s.deleted for s in sessions):
                raise RuntimeError("Native session has uncommitted changes")
            now, observed = utc(now), utc(target.observed_at)
            if not 0 <= (now-observed).total_seconds() <= 15:
                raise ValueError("Stale or future target snapshot")
            if (target.account_id, target.trade_id, target.pair) != (self.account_id, trade.id, trade.pair):
                raise ValueError("Target identity mismatch")
            if type(target.sequence) is not int or target.sequence < 0:
                raise ValueError("Nonnegative target sequence required")
            if trade.is_short or trade.leverage != 1:
                raise ValueError("Only frozen 1x long scope is supported")
            # Closed native Trade.amount retains historical size, not exposure.
            quantity = dec(trade.amount) if trade.is_open else Decimal(0)
            stake, cap = dec(trade.stake_amount), dec(target.max_remaining_quantity)
            if abs(quantity-dec(target.observed_quantity)) > EPSILON:
                raise ValueError("Target snapshot position differs from native holdings")
            if cap < 0 or quantity < 0 or stake < 0:
                raise ValueError("Negative quantity or stake")
            state = trade.get_custom_data(KEY)
            if state is None:
                state = {"schema": 1, "account_id": self.account_id, "trade_id": trade.id,
                         "pair": trade.pair, "target": None, "attempts": []}
            if (state["schema"], state["account_id"], state["trade_id"], state["pair"]) != (1, self.account_id, trade.id, trade.pair):
                raise ValueError("Stored reduction identity mismatch")
            descriptor = {"sequence": target.sequence, "cap": str(cap), "reason": target.reason}
            previous = state["target"]
            if previous:
                if target.sequence < previous["sequence"]:
                    raise ValueError("Target sequence went backward")
                if target.sequence == previous["sequence"] and descriptor != previous:
                    raise ValueError("Conflicting target at same sequence")
            # Any open entry or exit order requires upstream reconciliation first.
            if trade.has_open_orders:
                return None
            if state["attempts"] and state["attempts"][-1]["status"] == "ISSUED_UNKNOWN":
                attempt = state["attempts"][-1]
                orders = [o for o in trade.orders if o.ft_order_tag == attempt["tag"]]
                if not orders:
                    return None
                if len(orders) != 1:
                    raise ValueError("Ambiguous native reduction order")
                order = orders[0]
                if order.ft_pair != trade.pair or order.ft_order_side != trade.exit_side:
                    raise ValueError("Reduction order identity mismatch")
                if order.ft_is_open or order.status not in ("closed", "canceled", "expired", "rejected"):
                    return None
                if order.filled is None:
                    raise ValueError("Terminal order is missing its filled quantity")
                filled = dec(order.safe_amount_after_fee)
                if filled < 0 or filled > dec(attempt["requested_quantity"]) + EPSILON:
                    raise ValueError("Reduction fill outside requested quantity")
                expected = dec(attempt["quantity_before"]) - filled
                if abs(quantity-expected) > EPSILON:
                    raise ValueError("Native holdings do not reconcile with reduction fill")
                attempt.update(status="RECONCILED", order_id=order.order_id,
                               filled_quantity=str(filled), quantity_after=str(quantity))
                self._save(trade, state)
                if target.sequence == previous["sequence"]:
                    return None
            state["target"] = descriptor
            if not trade.is_open or quantity <= cap or quantity == 0:
                self._save(trade, state)
                return None
            if stake <= 0:
                raise ValueError("Positive native stake required for reduction")
            reduction = quantity - cap
            ordinal = len(state["attempts"]) + 1
            identity = f"{self.account_id}:{trade.id}:{target.sequence}:{ordinal}"
            tag = "rd1_reduce_" + hashlib.sha256(identity.encode()).hexdigest()[:24]
            state["attempts"].append({"tag": tag, "status": "ISSUED_UNKNOWN",
                                      "quantity_before": str(quantity), "requested_quantity": str(reduction),
                                      "target_quantity": str(cap), "observed_at": observed.isoformat()})
            self._save(trade, state)
            # Exactly the inverse of Freqtrade's stake -> exit-quantity formula.
            return -float(stake * reduction / quantity), tag
