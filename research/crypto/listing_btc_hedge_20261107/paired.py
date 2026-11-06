"""A BTC long funded alongside each new-listing short."""

import math
from collections import defaultdict
from dataclasses import dataclass

import pandas as pd

from research.crypto.listing_reversal_20261107 import replay as base


@dataclass
class Leg:
    quantity: float
    price: float
    side: int

    @property
    def collateral(self):
        return self.quantity * self.price


@dataclass
class Campaign:
    alt: Leg
    hedge: Leg
    opened: int
    expiry: int
    net_cash: float = 0.0
    pending_since: int | None = None


class PairedAccount(base.Account):
    def __init__(self, plan, cost, btc):
        super().__init__(plan, cost)
        self.btc = btc
        self.btc_turnover_remaining = 0.0
        self.hedge_fraction = plan["btc_notional_per_short"]

    def legs(self, symbol, campaign, markets):
        return ((markets[symbol], campaign.alt), (self.btc, campaign.hedge))

    def value(self, clock, markets):
        nav = self.cash
        for symbol, campaign in self.hold.items():
            for market, leg in self.legs(symbol, campaign, markets):
                if leg.quantity <= 0:
                    continue
                mark = market.mark_close[market.index(clock)]
                exit_rate = mark * (1 - leg.side * self.slip)
                nav += leg.quantity * (leg.side * (exit_rate - leg.price) - exit_rate * self.fee)
        return nav

    def add_event(self, clock, symbol, campaign, market, leg, kind, quantity, rate, delta, **more):
        self.book(
            clock,
            market.name,
            kind,
            quantity,
            rate,
            delta,
            campaign=symbol,
            side=leg.side,
            **more,
        )
        campaign.net_cash += delta

    def enter(self, clock, market, nav):
        used = math.fsum(p.alt.collateral + p.hedge.collateral for p in self.hold.values())
        total = min(
            self.plan["stake_fraction"] * nav,
            self.plan["margin_fraction"] * nav - used,
            (self.cash - used) / (1 + self.fee),
        )
        i, j = market.index(clock), self.btc.index(clock)
        participation = self.plan["volume_participation"]
        cap_alt = participation * min(market.quote[i], market.quote[i - base.HOUR : i].mean())
        cap_btc = min(
            self.btc_turnover_remaining,
            participation * self.btc.quote[j - base.HOUR : j].mean(),
        )
        alt_notional = min(
            total / (1 + self.hedge_fraction), cap_alt, cap_btc / self.hedge_fraction
        )
        if alt_notional <= 0:
            return
        btc_notional = self.hedge_fraction * alt_notional
        alt_rate = market.close[i] * (1 - self.slip)
        btc_rate = self.btc.close[j] * (1 + self.slip)
        campaign = Campaign(
            Leg(alt_notional / alt_rate, alt_rate, -1),
            Leg(btc_notional / btc_rate, btc_rate, 1),
            clock,
            clock + self.plan["hold_hours"] * base.HOUR,
        )
        for venue, leg in ((market, campaign.alt), (self.btc, campaign.hedge)):
            self.add_event(
                clock,
                market.name,
                campaign,
                venue,
                leg,
                "entry",
                leg.quantity,
                leg.price,
                -leg.collateral * self.fee,
            )
        self.btc_turnover_remaining -= btc_notional
        self.hold[market.name] = campaign

    def settle(self, clock, symbol, campaign, market, leg):
        for actual, rate, mark in market.funding.get(clock, ()):
            if pd.Timestamp(actual) > pd.Timestamp(base.iso(campaign.opened)):
                delta = -leg.side * leg.quantity * rate * mark
                self.add_event(
                    clock,
                    symbol,
                    campaign,
                    market,
                    leg,
                    "funding",
                    leg.quantity,
                    mark,
                    delta,
                    actual=actual,
                    funding=rate,
                )

    def exit_legs(self, clock, symbol, campaign, active):
        priced = []
        fraction = 1.0
        for market, leg in active:
            i = market.index(clock)
            rate = market.close[i] * (1 - leg.side * self.slip)
            cap = self.plan["volume_participation"] * market.quote[i]
            if market is self.btc:
                cap = min(cap, self.btc_turnover_remaining)
            fraction = min(fraction, cap / (leg.quantity * rate))
            priced.append((market, leg, rate))
        if fraction <= 0:
            return
        for market, leg, rate in priced:
            quantity = leg.quantity * fraction
            delta = quantity * (leg.side * (rate - leg.price) - rate * self.fee)
            self.add_event(clock, symbol, campaign, market, leg, "exit", quantity, rate, delta)
            leg.quantity -= quantity
            if market is self.btc:
                self.btc_turnover_remaining -= quantity * rate

    def positions(self, clock, markets):
        for symbol, campaign in list(self.hold.items()):
            active = [
                (m, leg) for m, leg in self.legs(symbol, campaign, markets) if leg.quantity > 0
            ]
            worst = []
            for market, leg in active:
                self.settle(clock, symbol, campaign, market, leg)
                i = market.index(clock)
                extreme = market.mark_high[i] if leg.side == -1 else market.mark_low[i]
                adverse = leg.side * (1 - extreme / leg.price)
                worst.append((adverse, market, leg))
            margin_loss = [
                (m, leg) for drop, m, leg in worst if drop >= self.plan["margin_loss_mark_increase"]
            ]
            if margin_loss:
                for market, leg in margin_loss:
                    self.add_event(
                        clock,
                        symbol,
                        campaign,
                        market,
                        leg,
                        "collateral_loss",
                        leg.quantity,
                        leg.price,
                        -leg.collateral,
                    )
                    leg.quantity = 0.0
                campaign.pending_since = clock
            elif clock > campaign.expiry or (
                campaign.pending_since is not None and clock > campaign.pending_since
            ):
                self.exit_legs(clock, symbol, campaign, active)
            if not (campaign.alt.quantity > 0 or campaign.hedge.quantity > 0):
                self.finished.append(
                    {
                        "symbol": symbol,
                        "entry": base.iso(campaign.opened),
                        "exit": base.iso(clock),
                        "net_usdt": campaign.net_cash,
                    }
                )
                del self.hold[symbol]
            elif campaign.pending_since is None and any(
                drop >= self.plan["stop_mark_increase"] for drop, _, _ in worst
            ):
                campaign.pending_since = clock

    def step(self, clock, markets, signals):
        self.btc_turnover_remaining = (
            self.plan["volume_participation"] * self.btc.quote[self.btc.index(clock)]
            if clock > self.btc.launch
            else 0.0
        )
        super().step(clock, markets, signals)


def simulate(plan, markets, cost, btc):
    account = PairedAccount(plan, cost, btc)
    signals = defaultdict(list)
    for market in markets.values():
        if market.day_quote >= plan["day_quote_minimum"]:
            signals[market.launch + base.DAY + 1].append(market)
    start, end = (
        base.bar_number(pd.Timestamp(plan[k], tz="UTC")) for k in ("common_start", "common_end")
    )
    for clock in range(start, end + 1):
        account.step(clock, markets, signals.get(clock, ()))
    if account.hold:
        raise ValueError("Unresolved paired exposure at terminal clock")
    return account, sum(map(len, signals.values()))
