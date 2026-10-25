"""Causal visible-book and actually received trade features, without labels."""

import json
from collections import Counter, deque

import numpy as np


FACTOR_NAMES = [
    "spread_bps",
    "imbalance_l1",
    "imbalance_l5",
    "imbalance_l20",
    "microprice_deviation_bps",
    "received_flow_30s",
    "received_flow_120s",
    "received_total_flow_30s",
    "received_rpi_share_30s",
    "persistence_30s",
    "flow_x_imbalance",
    "flow_x_microprice",
]


def visible_book(data):
    if data.get("e") != "depthUpdate" or data.get("st") != 1:
        raise ValueError("Expected UM depth snapshot")
    bids, asks = np.asarray(data["b"], dtype=float), np.asarray(data["a"], dtype=float)
    if bids.shape != (20, 2) or asks.shape != (20, 2):
        raise ValueError("Exactly20two-field visible levels required")
    if not np.isfinite(bids).all() or not np.isfinite(asks).all():
        raise ValueError("Nonfinite depth")
    if (bids <= 0).any() or (asks <= 0).any():
        raise ValueError("Full partial-depth snapshots require positive price and size")
    if (np.diff(bids[:, 0]) >= 0).any() or (np.diff(asks[:, 0]) <= 0).any():
        raise ValueError("Depth levels must be strictly price ordered")
    bid, ask = bids[0, 0], asks[0, 0]
    if bid >= ask:
        raise ValueError("Crossed or locked visible book")
    mid = (bid + ask) / 2
    q_bid, q_ask = bids[0, 1], asks[0, 1]
    result = {
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread_bps": 10_000 * (ask - bid) / mid,
        "imbalance_l1": (q_bid - q_ask) / (q_bid + q_ask),
        "microprice_deviation_bps": 10_000
        * ((ask * q_bid + bid * q_ask) / (q_bid + q_ask) / mid - 1),
    }
    for size in [5, 20]:
        b = (bids[:size, 0] * bids[:size, 1]).sum()
        a = (asks[:size, 0] * asks[:size, 1]).sum()
        result[f"imbalance_l{size}"] = (b - a) / (b + a)
    return result


def normal_trade(data):
    if data.get("e") != "aggTrade" or data.get("st") != 1 or not isinstance(data.get("m"), bool):
        raise ValueError("Expected UM aggregate trade with explicit aggressor flag")
    price, quantity, normal = [float(data[name]) for name in ["p", "q", "nq"]]
    if not np.isfinite([price, quantity, normal]).all() or price <= 0 or quantity <= 0:
        raise ValueError("Invalid trade price or quantity")
    if normal < 0 or normal > quantity:
        raise ValueError("Normal quantity must lie within total quantity")
    return {
        "id": int(data["a"]),
        "price": price,
        "quantity": quantity,
        "normal": normal,
        "sign": -1 if data["m"] else 1,
        "event_ms": int(data["E"]),
        "transaction_ms": int(data["T"]),
        "first_id": int(data["f"]),
        "last_id": int(data["l"]),
    }


def received_flow(trades, now, seconds, coverage_start, age_limit_ns):
    start = now - seconds * 1_000_000_000
    unknown = {"normal_flow": np.nan, "total_flow": np.nan, "rpi_share": np.nan}
    if coverage_start is None or coverage_start > start or not trades:
        return unknown
    if now - trades[-1]["received"] > age_limit_ns:
        return unknown
    selected = [trade for trade in trades if start < trade["received"] <= now]
    if not selected:
        return unknown
    total = sum(t["price"] * t["quantity"] for t in selected)
    normal = sum(t["price"] * t["normal"] for t in selected)
    normal_signed = sum(t["sign"] * t["price"] * t["normal"] for t in selected)
    total_signed = sum(t["sign"] * t["price"] * t["quantity"] for t in selected)
    return {
        "normal_flow": normal_signed / normal if normal > 0 else np.nan,
        "total_flow": total_signed / total,
        "rpi_share": (total - normal) / total,
    }


class FeatureBuilder:
    def __init__(self, plan):
        self.plan = plan
        self.spec = plan["feature_clock"]
        self.symbols = plan["symbols"]
        self.counts = Counter()
        self.state = {
            symbol: {
                "book": None,
                "trades": deque(),
                "trade_epoch": None,
                "last_trade": None,
                "coverage_start": None,
            }
            for symbol in self.symbols
        }
        self.rows = []
        self.anchor_mono = self.anchor_wall = self.next_decision = None
        self.previous_mono = self.previous_sequence = None
        self.wall_clock_invalid = False

    def ingest(self, record):  # noqa: C901
        now, wall = record["received_monotonic_ns"], record["received_wall_ns"]
        sequence = record["sequence"]
        if self.previous_sequence is not None and sequence != self.previous_sequence + 1:
            raise ValueError("Raw capture sequence gap")
        if self.previous_mono is not None and now < self.previous_mono:
            raise ValueError("Receive monotonic clock moved backwards")
        if self.anchor_mono is None:
            self.anchor_mono, self.anchor_wall = now, wall
            self.next_decision = now + self.spec["cadence_seconds"] * 1_000_000_000
        while self.next_decision < now:
            self.emit(self.next_decision)
            self.next_decision += self.spec["cadence_seconds"] * 1_000_000_000
        mismatch = abs((wall - self.anchor_wall) - (now - self.anchor_mono))
        if mismatch > self.spec["wall_clock_step_tolerance_ms"] * 1_000_000:
            self.wall_clock_invalid = True
            self.counts["wall_clock_step_records"] += 1
        self.previous_mono, self.previous_sequence = now, sequence
        data = json.loads(record["raw"])
        message = data["data"]
        symbol = message["s"]
        if symbol not in self.state:
            raise ValueError("Unregistered contract")
        state, channel = self.state[symbol], record["channel"]
        expected = self.plan["sources"]["depth_stream" if channel == "depth" else "trade_stream"]
        if data["stream"] != expected.format(symbol=symbol.lower()):
            raise ValueError("Combined stream identity mismatch")
        self.counts[f"{channel}:{symbol}"] += 1
        if channel == "depth":
            try:
                book = visible_book(message)
            except (ValueError, KeyError, TypeError):
                state["book"] = None
                self.counts[f"invalid_book:{symbol}"] += 1
                return
            prior = state["book"]
            if prior and prior["epoch"] == record["epoch"]:
                if int(message["pu"]) != prior["update_id"]:
                    self.counts[f"book_chain_gap:{symbol}"] += 1
                if int(message["u"]) <= prior["update_id"]:
                    self.counts[f"book_nonincreasing_id:{symbol}"] += 1
                    return
            state["book"] = dict(
                book,
                received=now,
                epoch=record["epoch"],
                update_id=int(message["u"]),
                event_ms=int(message["E"]),
            )
        elif channel == "trades":
            trade = normal_trade(message)
            prior = state["last_trade"]
            reset = state["trade_epoch"] != record["epoch"]
            if prior and not reset and trade["id"] <= prior["id"]:
                equivalent = all(trade[name] == prior[name] for name in trade)
                if trade["id"] == prior["id"] and equivalent:
                    self.counts[f"duplicate_trade:{symbol}"] += 1
                    return
                self.counts[f"conflicting_or_reordered_trade:{symbol}"] += 1
                state["trades"].clear()
                state["coverage_start"] = now
                return
            if prior and not reset and trade["id"] != prior["id"] + 1:
                self.counts[f"trade_id_gap:{symbol}"] += 1
                reset = True
            if reset:
                state["trades"].clear()
                state["coverage_start"] = now
            state["trade_epoch"] = record["epoch"]
            trade["received"] = now
            state["last_trade"] = trade
            state["trades"].append(trade)
        else:
            raise ValueError("Unknown capture channel")

    def emit(self, now):
        for symbol, state in self.state.items():
            values = dict.fromkeys(FACTOR_NAMES, np.nan)
            book = state["book"]
            book_age = (now - book["received"]) / 1e6 if book else np.nan
            if book and 0 <= book_age <= self.spec["book_maximum_age_ms"]:
                values.update(
                    {
                        name: book[name]
                        for name in [
                            "spread_bps",
                            "imbalance_l1",
                            "imbalance_l5",
                            "imbalance_l20",
                            "microprice_deviation_bps",
                            "bid",
                            "ask",
                            "mid",
                        ]
                    }
                )
            else:
                values.update(dict.fromkeys(["bid", "ask", "mid"], np.nan))
            trades = state["trades"]
            while trades and trades[0]["received"] <= now - 120_000_000_000:
                trades.popleft()
            age_limit = self.spec["trade_maximum_age_ms"] * 1_000_000
            flows = {
                seconds: received_flow(trades, now, seconds, state["coverage_start"], age_limit)
                for seconds in self.spec["window_seconds"]
            }
            values["received_flow_30s"] = flows[30]["normal_flow"]
            values["received_flow_120s"] = flows[120]["normal_flow"]
            values["received_total_flow_30s"] = flows[30]["total_flow"]
            values["received_rpi_share_30s"] = flows[30]["rpi_share"]
            if np.isfinite(values["received_flow_30s"]):
                bins = []
                for offset in range(6):
                    end = now - offset * 5_000_000_000
                    chosen = [t for t in trades if end - 5_000_000_000 < t["received"] <= end]
                    volume = sum(t["price"] * t["normal"] for t in chosen)
                    signed = sum(t["sign"] * t["price"] * t["normal"] for t in chosen)
                    bins.append(np.sign(signed) if volume > 0 else np.nan)
                if np.isfinite(bins).all():
                    values["persistence_30s"] = float(np.mean(bins))
            values["flow_x_imbalance"] = values["received_flow_30s"] * values["imbalance_l5"]
            values["flow_x_microprice"] = (
                values["received_flow_30s"] * values["microprice_deviation_bps"]
            )
            complete = bool(np.isfinite([values[name] for name in FACTOR_NAMES]).all())
            self.rows.append(
                {
                    "symbol": symbol,
                    "decision_monotonic_ns": now,
                    "decision_wall_ns": self.anchor_wall + now - self.anchor_mono,
                    "last_processed_sequence": self.previous_sequence,
                    "book_age_ms": book_age,
                    "trade_age_ms": (
                        (now - state["last_trade"]["received"]) / 1e6
                        if state["last_trade"]
                        else np.nan
                    ),
                    "wall_clock_valid": not self.wall_clock_invalid,
                    "complete_factors": complete and not self.wall_clock_invalid,
                    **values,
                }
            )

    def finish(self, end_monotonic_ns):
        if self.next_decision is None:
            return self.rows
        if end_monotonic_ns < self.previous_mono:
            raise ValueError("Feature end precedes last received message")
        while self.next_decision <= end_monotonic_ns:
            self.emit(self.next_decision)
            self.next_decision += self.spec["cadence_seconds"] * 1_000_000_000
        return self.rows
