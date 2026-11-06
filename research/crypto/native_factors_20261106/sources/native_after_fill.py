"""Observed native after-fill updates with a durable local protection barrier.

Only an offline exchange calculator is accepted. No exchange-side stop, live
liquidation price, historical tier provenance or complete account NAV is certified.
"""
import copy
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace
import threading

from freqtrade.enums import MarginMode, RunMode, TradingMode
from freqtrade.exchange.binance import Binance
from freqtrade.freqtradebot import FreqtradeBot
from freqtrade.persistence import Trade
from native_entries import LocalBinanceTape
from native_evidence import decimal_text
from rd1 import RiskError, StateError, Store, aware, dec, digest, primitive


class OfflineProtectionExchange(Binance):
    def __init__(self, market, markets, tiers, source_id):
        if not source_id or market not in ("spot", "futures"):
            raise RiskError("Explicit offline market/rules source required")
        self._api = LocalBinanceTape(markets, [])
        self._markets = self._api.markets
        self._leverage_tiers = copy.deepcopy(tiers)
        self._config = {"dry_run": True, "runmode": RunMode.BACKTEST, "stake_currency": "USDT"}
        self.trading_mode = TradingMode(market)
        self.margin_mode = MarginMode.ISOLATED if market == "futures" else MarginMode.NONE
        self.liquidation_buffer = .05  # fixed official default, not a tuned parameter
        self._is_demo_trading = False
        self.rules_source = source_id
        self.rules_hash = digest({"markets": markets, "tiers": tiers, "liquidation_buffer": .05})

    def __del__(self):
        pass

    def close(self):
        self._api.session.close()

    def create_order(self, **kwargs):
        raise RiskError("Offline protection calculator cannot submit orders")

    def fetch_positions(self, *args, **kwargs):
        raise RiskError("Offline protection calculator cannot read private positions")

    def cancel_stoploss_order_with_result(self, *args, **kwargs):
        raise RiskError("Exchange-side stops are outside this offline adapter")


def order_state(order):
    return primitive({k: getattr(order, k) for k in ("id", "order_id", "ft_pair", "ft_order_side", "status",
                       "amount", "filled", "remaining", "average", "ft_fee_base", "ft_is_open")})


def trade_state(trade):
    return primitive({k: getattr(trade, k) for k in ("id", "pair", "is_open", "amount", "stake_amount", "open_rate",
                       "stop_loss", "stop_loss_pct", "initial_stop_loss", "liquidation_price")})


class _Observer:
    def __init__(self, delegate, guard, event):
        self.delegate, self.guard, self.event = delegate, guard, event

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def run(self, stage, function, *args, **kwargs):
        self.guard.stage(self.event, stage, "STARTED")
        try:
            result = function(*args, **kwargs)
        except Exception as exc:
            self.guard.stage(self.event, stage, "ERROR", error=type(exc).__name__)
            raise
        self.guard.stage(self.event, stage, "RETURNED", result=result if stage == "liquidation" else None)
        return result


class _StrategyObserver(_Observer):
    def order_filled(self, **kwargs):
        return self.run("order_filled", self.delegate.order_filled, **kwargs)


class _ExchangeObserver(_Observer):
    def get_liquidation_price(self, **kwargs):
        return self.run("liquidation", self.delegate.get_liquidation_price, **kwargs)


class _WalletObserver(_Observer):
    def update(self, *args, **kwargs):
        return self.run("wallet_update", self.delegate.update, *args, **kwargs)


class _BotObserver(_Observer):
    def __init__(self, bot, guard, event, before_quantity):
        super().__init__(bot, guard, event)
        self.strategy = _StrategyObserver(bot.strategy, guard, event)
        self.exchange = _ExchangeObserver(bot.exchange, guard, event)
        self.wallets = _WalletObserver(bot.wallets, guard, event)
        self.before_quantity = before_quantity

    def order_close_notify(self, trade, order, stoploss_order, send_msg):
        required=self.guard.s.get('required_native_protections')
        if required is not None and order.ft_order_side==trade.exit_side and not trade.is_open:
            runtime=getattr(self.guard,'native_protections',None)
            if runtime is None or runtime.binding!=required:
                raise RiskError('Registered native protections must be reattached after restart')
            if send_msg:raise RiskError('Local protection observer cannot send external notifications')
            proxy=SimpleNamespace(handle_protections=lambda pair,side:self.run('native_protections',runtime.apply_closed,
                self.event,self.delegate,trade,order,self.delegate.wallets._last_wallet_refresh))
            return FreqtradeBot.order_close_notify(proxy,trade,order,stoploss_order,False)
        return self.delegate.order_close_notify(trade,order,stoploss_order,send_msg)

    def _update_trade_after_fill(self, trade, order, send_msg):
        result = FreqtradeBot._update_trade_after_fill(self, trade, order, send_msg)
        if order.status != "open" and order.safe_filled > 0 and trade.is_open and trade.amount > 0:
            # First completed entry uses actual fill basis, including price improvement.
            # Later exits may tighten but never reset an existing tighter stop.
            first = self.before_quantity == 0 and order.ft_order_side == trade.entry_side
            trade.adjust_stop_loss(trade.open_rate, -float(self.guard.stop_distance), allow_refresh=first)
        self.guard.stage(self.event, "after_fill_hook", "RETURNED", post_trade=trade_state(trade), post_order=order_state(order))
        return result


class AfterFillGuard:
    def __init__(self, directory, risk, *, create=False):
        self.identity = dict(risk.s["identity"])
        self.market = risk.market
        self.stop_distance = dec(risk.r["stop_price_distance_fraction"])
        self.engine = Trade.session.get_bind()
        self._mutex = threading.RLock(); self._poisoned = False
        self.store = Store(Path(directory), dict(self.identity, layer="native_after_fill_v1"), create=create)
        if create:
            self.s = {"schema_version": 1, "sequence": 0, "identity": self.store.identity, "events": {}}
            self._persist()
        else:
            self.s = self.store.load()

    def close(self):
        self.store.close()

    def _persist(self):
        if self._poisoned or self.store.lock.closed:
            raise StateError("Protection writer unavailable")
        self.s["sequence"] += 1
        try:
            self.store.save(self.s)
        except Exception as exc:
            self._poisoned = True
            raise StateError("Protection record not durably acknowledged; reopen and verify") from exc

    def stage(self, event, name, state, **values):
        self.s["events"][event]["stages"][name] = primitive(dict(state=state, **values))
        self._persist()

    def _database_ready(self):
        if not Trade.use_db or Trade.session.get_bind() is not self.engine:
            raise RiskError("Protection native database binding changed")
        if Trade.session.new or Trade.session.dirty or Trade.session.deleted:
            raise RiskError("Protection requires committed native state")

    def require_entry_clear(self):
        with self._mutex:
            if self._poisoned or self.store.lock.closed:
                raise StateError("Protection writer unavailable")
            self._database_ready()
            if any(v["state"] not in ("VERIFIED", "SUPERSEDED_PARTIAL") for v in self.s["events"].values()):
                raise RiskError("Unresolved after-fill protection blocks new risk")
            trades = Trade.get_trades_proxy()
            if any(v["trade_id"] not in {t.id for t in trades} for v in self.s["events"].values()):
                raise RiskError("Native trade disappeared from protection inventory")
            for trade in trades:
                for order in trade.orders:
                    if order.safe_filled > 0 and not any(
                        v["state"] == "VERIFIED" and v["trade_id"] == trade.id and v.get("verified_order") == order_state(order)
                        for v in self.s["events"].values()
                    ):
                        raise RiskError("Native fill lacks matching protection evidence")
                events = [v for v in self.s["events"].values() if v["trade_id"] == trade.id and v["state"] == "VERIFIED"]
                if events and trade_state(trade) != max(events, key=lambda e:e["started_sequence"])["verified_trade"]:
                    raise RiskError("Native position/protection changed after verification")

    def verify_committed(self, event):
        with self._mutex:
            if self._poisoned or self.store.lock.closed:
                raise StateError("Protection writer unavailable")
            self._database_ready()
            item = self.s["events"][event]; stages = item["stages"]
            trade = Trade.session.get(Trade, item["trade_id"])
            if trade is None:
                raise RiskError("Native trade disappeared from protection inventory")
            order = trade.select_order_by_order_id(item["order_id"])
            if order is None or not stages.get("after_fill_hook"):
                raise RiskError("After-fill completion has no durable proof")
            post = stages["after_fill_hook"]
            if trade_state(trade) != post["post_trade"] or order_state(order) != post["post_order"]:
                raise RiskError("Committed native state differs from after-fill result")
            if order.status == "open" and order.safe_filled > 0:
                raise RiskError("Open partial fill has no native terminal protection")
            for name in ("order_filled", "wallet_update"):
                if stages.get(name, {}).get("state") != "RETURNED":
                    raise RiskError("Native after-fill stage failed or missing: " + name)
            required=self.s.get('required_native_protections')
            if required is not None and not trade.is_open and order.ft_order_side==trade.exit_side:
                runtime=getattr(self,'native_protections',None)
                if runtime is None or runtime.binding!=required:
                    raise RiskError('Registered native protections must be reattached after restart')
                runtime.verify_closed(event,trade,order)
                if stages.get('native_protections',{}).get('state')!='RETURNED':
                    self.stage(event,'native_protections','RETURNED',recovery='committed_native_locks_verified')
            if trade.is_open and trade.amount > 0:
                stop = dec(decimal_text(trade.stop_loss))
                floor = dec(decimal_text(trade.open_rate)) * (1-self.stop_distance)
                if stop < floor:
                    raise RiskError("Native stop is weaker than frozen RD1 floor")
                liquid = stages.get("liquidation", {})
                if liquid.get("state") != "RETURNED":
                    raise RiskError("Liquidation refresh failed or was suppressed")
                if self.market == "futures" and (liquid.get("result") is None or trade.liquidation_price is None):
                    raise RiskError("Futures liquidation value missing; stale value cannot certify refresh")
                if self.market == "futures" and stop <= dec(decimal_text(trade.liquidation_price)):
                    raise RiskError("Stop does not precede buffered liquidation price")
            item.update(state="VERIFIED", verified_trade=trade_state(trade), verified_order=order_state(order))
            # A later terminal report can resolve an earlier open partial for this
            # same order, whose terminal callbacks were never run.
            for previous in self.s["events"].values():
                if (previous is not item and previous["trade_id"] == trade.id and previous["order_id"] == order.order_id
                        and previous["state"] == "OPEN_PARTIAL_PENDING"):
                    previous["state"] = "SUPERSEDED_PARTIAL"
            self._persist()
            return copy.deepcopy(item)

    def apply(self, event, bot, trade, envelope, now, *, return_pending_partial=False):
        with self._mutex:
            self._database_ready()
            if self._poisoned or self.store.lock.closed:
                raise StateError("Protection writer unavailable")
            if not isinstance(event, str) or not event:
                raise RiskError("Protection event identity required")
            if trade.id is None or Trade.session.get(Trade, trade.id) is not trade:
                raise RiskError("Trade is not attached to the guarded native database")
            if type(bot.exchange) is not OfflineProtectionExchange or bot.exchange.trading_mode.value != self.market:
                raise RiskError("Only matching offline native protection calculator supported")
            if (dec(decimal_text(bot.strategy.stoploss)) != -self.stop_distance or bot.strategy.use_custom_stoploss
                    or bot.strategy.trailing_stop or bot.strategy.order_types.get("stoploss_on_exchange")):
                raise RiskError("Strategy protection differs from frozen offline RD1 contract")
            if trade.is_short or trade.leverage != 1 or trade.exchange != "binance" or trade.open_sl_orders:
                raise RiskError("Unsupported native protection account/order scope")
            if envelope.evidence_class != "synthetic" or not 0 <= (aware(now)-aware(envelope.received_at)).total_seconds() <= 15:
                raise RiskError("Explicit fresh synthetic fill receipt required")
            report = envelope.normalize(); raw = json.loads(envelope.payload_json)
            order = trade.select_order_by_order_id(report["id"])
            if (order is None or report["symbol"] != trade.pair or report["side"] != order.side
                    or dec(report["amount"]) != dec(decimal_text(order.amount))
                    or dec(report["price"]) != dec(decimal_text(order.price))
                    or report["status"] not in ("open", "closed", "canceled", "expired", "rejected")
                    or dec(report["remaining"]) < 0
                    or dec(report["filled"]) < dec(decimal_text(order.safe_filled))
                    or dec(report["filled"])+dec(report["remaining"]) != dec(report["amount"])):
                raise RiskError("Fill update differs from native order")
            if dec(report["filled"]) <= 0:
                raise RiskError("Zero-fill outcomes use lifecycle reconciliation, not after-fill")
            fee = raw.get("fee")
            if (not isinstance(fee, dict) or fee.get("currency") != "USDT" or fee.get("cost") is None
                    or dec(decimal_text(fee["cost"])) < 0 or raw.get("average") is None
                    or dec(decimal_text(raw["average"])) <= 0 or raw.get("cost") is None
                    or dec(decimal_text(raw["cost"])) <= 0):
                raise RiskError("Explicit quote fee and actual average required for this protection path")
            if order.order_type == "limit" and (
                (order.side == "buy" and dec(decimal_text(raw["average"])) > dec(report["price"]))
                or (order.side == "sell" and dec(decimal_text(raw["average"])) < dec(report["price"]))
            ):
                raise RiskError("Average fill violates native limit price")
            signature = digest({"trade_id":trade.id, "envelope":asdict(envelope), "rules_hash":bot.exchange.rules_hash})
            if event in self.s["events"]:
                if self.s["events"][event]["signature"] != signature:
                    raise StateError("Conflicting after-fill event")
                previous = self.s["events"][event]
                if return_pending_partial is True and previous["state"] == "OPEN_PARTIAL_PENDING":
                    post = previous["stages"].get("after_fill_hook", {})
                    if (post.get("post_trade") != trade_state(trade)
                            or post.get("post_order") != order_state(order)):
                        raise RiskError("Pending partial native state changed; fresh observation required")
                    return copy.deepcopy(previous)  # pending is never protection permission
                return self.verify_committed(event)  # never rerun an uncertain callback
            for prior in self.s["events"].values():
                if prior["trade_id"] == trade.id and prior["order_id"] == order.order_id and prior["state"] != "OPEN_PARTIAL_PENDING":
                    raise RiskError("Order already observed; recover the existing event")
            self.s["events"][event] = {"state":"PENDING", "trade_id":trade.id, "order_id":order.order_id,
                "signature":signature, "source":primitive(asdict(envelope)), "rules_hash":bot.exchange.rules_hash,
                "started_sequence":self.s["sequence"]+1, "stages":{}}
            self._persist()
            observer = _BotObserver(bot, self, event, trade.amount if trade.is_open else 0)
            try:
                FreqtradeBot.update_trade_state(observer, trade, order.order_id, raw, send_msg=False)
                if order.status == "open":
                    self.s["events"][event]["state"] = "OPEN_PARTIAL_PENDING"
                    self._persist()
                    if return_pending_partial is True:
                        return copy.deepcopy(self.s["events"][event])
                return self.verify_committed(event)
            except Exception:
                Trade.session.rollback()  # earlier native commits remain; never claim whole-transaction rollback
                raise
