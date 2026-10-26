"""Read sparse reported calendars without filling prices or certifying history."""

import hashlib
import json

import numpy as np
import pandas as pd


class UnknownHeldSource(ValueError):
    pass


class Marks(dict):
    def __init__(self, book, offset):
        self.book, self.offset = book, offset

    def __missing__(self, symbol):
        if not self.book.has_price(symbol, self.offset):
            raise UnknownHeldSource("Missing actual held/reference price: " + symbol)
        row = (
            self.book.preceding_bars[symbol]
            if self.offset == -1
            else self.book.data[symbol][self.offset]
        )
        price = float(row[3])
        self[symbol] = price
        return price


class SourceBook:
    def __init__(self, bars, times):
        if not len(times):
            raise ValueError("Empty account calendar")
        self.bars, self.times = bars, times
        self.data, self.price, self.executable, self.preceding_bars = {}, {}, {}, {}
        self.preceding_usable = {}
        columns = ["open", "high", "low", "close", "quote_volume"]
        for s, frame in bars.items():
            if (
                frame.index.tz is None
                or frame.index.has_duplicates
                or not frame.index.is_monotonic_increasing
            ):
                raise ValueError("Source index must be aware, unique and ordered")
            if not {"price_usable", "execution_liquidity_usable"}.issubset(frame):
                raise ValueError("Sparse execution requires explicit source quality flags")
            view = frame.reindex(times)
            self.data[s] = view[columns].to_numpy(dtype=float)
            self.price[s] = (
                view.price_usable.eq(True).to_numpy()
                & np.isfinite(self.data[s][:, :4]).all(axis=1)
                & (self.data[s][:, :4] > 0).all(axis=1)
            )
            self.executable[s] = (
                self.price[s]
                & view.execution_liquidity_usable.eq(True).to_numpy()
                & np.isfinite(self.data[s][:, 4])
                & (self.data[s][:, 4] > 0)
            )
            preceding = frame.reindex([times[0] - pd.Timedelta(minutes=5)])
            self.preceding_bars[s] = preceding[columns].to_numpy(dtype=float)[0]
            self.preceding_usable[s] = bool(
                preceding.price_usable.eq(True).iloc[0]
                and np.isfinite(self.preceding_bars[s][:4]).all()
                and (self.preceding_bars[s][:4] > 0).all()
            )

    def has_price(self, symbol, offset):
        return self.preceding_usable[symbol] if offset == -1 else bool(self.price[symbol][offset])

    def marks(self, offset):
        return Marks(self, offset)

    def can_execute(self, symbol, offset):
        return bool(self.executable[symbol][offset])

    def prior_quote_per_bar(self, symbol, offset):
        now = self.times[offset]
        expected = pd.date_range(now - pd.Timedelta(hours=1), now, freq="5min", inclusive="left")
        rows = self.bars[symbol].reindex(expected)
        quotes = rows.quote_volume.to_numpy(dtype=float)
        if (
            not rows.price_usable.eq(True).all()
            or not np.isfinite(quotes).all()
            or (quotes < 0).any()
        ):
            return 0.0
        # A zero-volume actual row contributes zero; a missing row never does.
        if ((quotes > 0) & ~rows.execution_liquidity_usable.eq(True)).any():
            return 0.0
        return float(quotes.sum() / 12)


class LifecyclePolicy:
    def __init__(self, directives, symbols):
        if set(directives) - set(symbols):
            raise ValueError("Lifecycle directive has foreign symbol")
        self.identity = hashlib.sha256(
            json.dumps(directives, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        self.rows, self.clocks, self.defaults = {}, {}, {}
        for s in symbols:
            rows = directives.get(s, [])
            clocks = pd.DatetimeIndex(
                pd.to_datetime([r["known_at"] for r in rows], utc=True)
            ).as_unit("ns")
            if clocks.has_duplicates or not clocks.is_monotonic_increasing:
                raise ValueError("Lifecycle directives must advance availability")
            for r in rows:
                for key in ["known_at", "entry_stop_at", "terminal_at"]:
                    if r.get(key) is not None and pd.Timestamp(r[key]).tz is None:
                        raise ValueError("Lifecycle clock must be timezone aware")
                if not isinstance(r["episode_key"], str) or not r["episode_key"].startswith(
                    s + ":"
                ):
                    raise ValueError("Lifecycle episode does not belong to symbol")
                if (
                    r.get("entry_stop_at")
                    and r.get("terminal_at")
                    and pd.Timestamp(r["entry_stop_at"]) > pd.Timestamp(r["terminal_at"])
                ):
                    raise ValueError("Entry cutoff after terminal")
            self.rows[s] = [
                {
                    **r,
                    "_entry_ns": pd.Timestamp(r["entry_stop_at"]).value
                    if r.get("entry_stop_at")
                    else None,
                    "_terminal_ns": pd.Timestamp(r["terminal_at"]).value
                    if r.get("terminal_at")
                    else None,
                }
                for r in rows
            ]
            self.clocks[s] = clocks.asi8
            self.defaults[s] = {
                "episode_key": s + ":observed_initial",
                "blocked": False,
                "_entry_ns": None,
                "_terminal_ns": None,
            }

    def at(self, symbol, clock):
        clocks = self.clocks[symbol]
        index = clocks.searchsorted(clock.value, side="right") - 1 if len(clocks) else -1
        return self.rows[symbol][index] if index >= 0 else self.defaults[symbol]

    def episode(self, symbol, clock):
        return self.at(symbol, clock)["episode_key"]

    def entry_blocked(self, symbol, clock):
        row = self.at(symbol, clock)
        return bool(
            row.get("blocked")
            or any(
                row.get(k) is not None and clock.value >= row[k]
                for k in ["_entry_ns", "_terminal_ns"]
            )
        )

    def require_inventory(self, symbol, position, clock):
        row = self.at(symbol, clock)
        if position.get("episode_key") != row["episode_key"]:
            raise UnknownHeldSource(
                "Old inventory cannot transfer to a different contract identity: " + symbol
            )
        if row.get("_terminal_ns") is not None and clock.value >= row["_terminal_ns"]:
            raise UnknownHeldSource(
                "Residual inventory has unknown terminal cash before an archive fill: " + symbol
            )
