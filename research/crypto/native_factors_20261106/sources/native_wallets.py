"""Actual native Wallets fed solely by explicit local account receipts.

Staging permits after-fill wallet refresh; it never permits entry. Verification
requires the same source account checkpoint to pass the durable account provider.
The attached entry barrier remains pending through cross-store failure windows.
"""
import copy
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
import threading

from freqtrade.enums import RunMode, TradingMode, MarginMode
from freqtrade.exchange.binance import Binance
from freqtrade.persistence import Trade
from freqtrade.wallets import Wallets
from native_entries import LocalBinanceTape
from native_clock import NativeReplayClock
from native_evidence import decimal_text
from native_snapshot import EPS
from rd1 import RiskError, StateError, Store, aware, dec, digest, primitive, stamp


@dataclass(frozen=True)
class WalletReceipt:
    account_id: str
    market: str
    source_id: str
    evidence_class: str
    received_at: datetime
    balances_at: datetime
    positions_at: datetime
    balance_semantics: str
    balances: dict
    positions: tuple[dict, ...]
    balances_complete: bool
    positions_complete: bool


class _LocalWalletSource(Binance):
    def __init__(self, markets, market, clock):
        # No Exchange initialization, private client, socket or request transport.
        self._api = LocalBinanceTape(markets, [])
        self._markets = self._api.markets
        self._config = {"stake_currency": "USDT"}
        self._ft_has = {"balance_includes_unrealized_pnl": False}
        self.trading_mode = TradingMode(market)
        self.margin_mode = MarginMode.ISOLATED if market == "futures" else MarginMode.NONE
        self.clock = clock; self.frame = None; self.now = None

    def __del__(self):
        pass

    def close(self):
        self._api.session.close()

    def _ready(self):
        if self.frame is None:
            raise RiskError("No explicit local wallet source receipt")
        self.clock.require(self.now)

    def get_balances(self):
        self._ready()
        return copy.deepcopy(self.frame["balances"])

    def fetch_positions(self, *args, **kwargs):
        self._ready()
        return copy.deepcopy(self.frame["positions"])

    def create_order(self, **kwargs):
        raise RiskError("Local wallet source cannot submit orders")


class NativeWalletBridge:
    def __init__(self, directory, provider, markets, *, create=False):
        self.provider = provider; self.identity = provider.risk.s["identity"]
        self.market = provider.market; self.clock = NativeReplayClock()
        self.source = _LocalWalletSource(markets, self.market, self.clock)
        self._native_market_hash = digest(self.source.markets)
        self.market_hash = digest(markets)
        self.store = Store(Path(directory), dict(self.identity, layer="native_wallet_bridge_v1", market_hash=self.market_hash,
            provider_identity_hash=digest(provider.store.identity)), create=create)
        self._poisoned = False; self._mutex = threading.RLock(); self.wallets = None; self._checked_evidence = None
        if create:
            self.s = {"schema_version": 1, "sequence": 0, "identity": self.store.identity,
                      "state": "BOOTSTRAP_PENDING", "frames": {}, "current": None}
            self._persist()
        else:
            self.s = self.store.load()
        # A persisted VERIFIED marker alone does not bootstrap a new native wallet.

    def close(self):
        self.source.close(); self.store.close()

    def attach(self, gate):
        if gate.lifecycle is not self.provider.bridge.reservations or gate.after_fill_guard is None:
            raise RiskError("Wallet barrier requires the same native lifecycle and after-fill guard")
        if gate.runtime_barrier not in (None, self):
            raise StateError("A different runtime barrier is already attached")
        gate.bind_runtime_barrier(self)

    def _ready(self, now):
        if self._poisoned or self.store.lock.closed:
            raise StateError("Wallet bridge writer unavailable")
        self.clock.require(now)
        self.provider._ready()

    def _persist(self):
        if self._poisoned or self.store.lock.closed:
            raise StateError("Wallet bridge writer unavailable")
        self.s["sequence"] += 1
        try:
            self.store.save(self.s)
        except Exception as exc:
            self._poisoned = True
            raise StateError("Wallet receipt commit not acknowledged; reopen and reconcile") from exc

    def _validate(self, receipt, evidence, now):
        snap = evidence.snapshot
        if (receipt.account_id, receipt.market) != (self.identity["account_id"], self.market):
            raise RiskError("Wallet account identity differs")
        if receipt.evidence_class != "synthetic" or evidence.evidence_class != "synthetic" or not receipt.source_id:
            raise RiskError("Only explicit synthetic local wallet evidence is supported")
        if receipt.balances_complete is not True or receipt.positions_complete is not True:
            raise RiskError("Complete native wallet source inventory required")
        pairs = {p.pair for p in snap.positions} | {o.pair for o in snap.orders}
        if not pairs <= set(self.source.markets):
            raise RiskError("Wallet source lacks explicit market metadata for account inventory")
        if (receipt.balance_semantics != snap.balance_semantics or receipt.balances_at != snap.balance_at
                or receipt.positions_at != snap.orders_at or receipt.received_at != snap.at
                or not 0 <= (aware(now)-aware(receipt.received_at)).total_seconds() <= 15
                or any(not 0 <= (aware(receipt.received_at)-aware(t)).total_seconds() <= 15 for t in (receipt.balances_at, receipt.positions_at))):
            raise RiskError("Wallet source chronology/semantics differ from account evidence")
        if receipt.balance_semantics != ("total_cash_including_reserved" if self.market == "spot" else "wallet_excluding_upnl_including_collateral_and_settled_flows"):
            raise RiskError("Wallet balance includes unknown collateral or unrealized PnL semantics")
        balances = {}
        for currency, values in receipt.balances.items():
            if not isinstance(currency, str) or not currency or not isinstance(values, dict) or set(values) != {"free", "used", "total"}:
                raise RiskError("Explicit free/used/total balances required; no default zero")
            free, used, total = (dec(values[k]) for k in ("free", "used", "total"))
            if min(free, used, total) < 0 or abs(free+used-total) > EPS:
                raise RiskError("Wallet free/used/total decomposition invalid")
            balances[currency] = {k: float(dec(v)) for k, v in values.items()}
        if "USDT" not in balances or abs(dec(receipt.balances["USDT"]["total"])-dec(snap.balance)) > EPS:
            raise RiskError("Native quote wallet differs from observed account cash")
        positions = {p.pair: p for p in snap.positions}
        if len(positions) != len(snap.positions):
            raise RiskError("Multiple account positions per native pair unsupported")
        required_cash = sum((abs(dec(o.remaining_quantity))*dec(o.limit_price) for o in snap.orders if o.increases_risk), D(0))
        parsed = []
        if self.market == "spot":
            if receipt.positions:
                raise RiskError("Spot wallet cannot contain derivatives positions")
            expected = {"USDT": dec(snap.balance)}; reserved = {}
            for pair, p in positions.items():
                base = self.source.get_pair_base_currency(pair)
                if base in expected:
                    raise RiskError("Ambiguous spot base currency inventory")
                expected[base] = dec(p.quantity)
            for order in snap.orders:
                if not order.increases_risk:
                    base = self.source.get_pair_base_currency(order.pair)
                    reserved[base] = reserved.get(base, D(0))+abs(dec(order.remaining_quantity))
            for currency in set(expected)|set(receipt.balances):
                values = receipt.balances.get(currency)
                if values is None or abs(dec(values["total"])-expected.get(currency,D(0))) > EPS:
                    raise RiskError("Spot base wallet differs from complete account positions")
                if currency != "USDT" and abs(dec(values["used"])-reserved.get(currency,D(0))) > EPS:
                    raise RiskError("Spot exit reservation differs from explicit open orders")
        else:
            if any(k != "USDT" and dec(v["total"]) != 0 for k,v in receipt.balances.items()):
                raise RiskError("Unexpected futures collateral asset")
            seen = set()
            for item in receipt.positions:
                needed = {"symbol","side","contracts","leverage","initialMargin","unrealizedPnl","entryPrice"}
                if not needed <= set(item) or item["symbol"] in seen or item["symbol"] not in positions:
                    raise RiskError("Missing, duplicate or unknown futures wallet position")
                pair = item["symbol"]; seen.add(pair); p = positions[pair]
                contracts = dec(item["contracts"]); size = dec(decimal_text(self.source._contracts_to_amount(pair,float(contracts))))
                if (item["side"] != "long" or dec(item["leverage"]) != 1 or contracts <= 0
                        or abs(size-dec(p.quantity)) > EPS or dec(item["entryPrice"]) != dec(p.entry_price)
                        or dec(item["initialMargin"]) != dec(p.collateral) or dec(p.collateral) < dec(p.quantity)*dec(p.entry_price)):
                    raise RiskError("Native futures wallet quantity, entry basis or 1x collateral differs")
                quotes = {q.pair:q for q in snap.quotes}
                if pair not in quotes or abs(dec(item["unrealizedPnl"])-dec(p.quantity)*(dec(quotes[pair].price)-dec(p.entry_price))) > EPS:
                    raise RiskError("Futures source unrealized PnL differs from explicit mark")
                required_cash += dec(p.collateral)
                parsed.append({k: (v if k in ("symbol","side") else float(dec(v))) for k,v in item.items() if k in needed})
            if seen != set(positions):
                raise RiskError("Futures wallet omits held positions")
        if dec(receipt.balances["USDT"]["used"])+EPS < required_cash:
            raise RiskError("Wallet used cash omits entry reservations or 1x collateral")
        return {"balances":balances,"positions":parsed}

    def stage(self, event, receipt, evidence, now):
        with self._mutex:
            self._ready(now)
            if not isinstance(event,str) or not event:
                raise RiskError("Wallet event identity required")
            self.s.update(state="ACCOUNT_CHECK_PENDING",stage_validated=False)
            self._checked_evidence = None
            self._persist()  # a failed fresh observation cannot leave old entry permission
            frame = self._validate(receipt,evidence,now)
            raw = primitive({"receipt":asdict(receipt),"evidence":asdict(evidence)})
            signature = digest(raw)
            prior = self.s["frames"].get(event)
            if prior and prior["signature"] != signature:
                raise RiskError("Conflicting wallet source event")
            if self.s["current"] is not None:
                previous = self.s["frames"][self.s["current"]]["raw"]["receipt"]["received_at"]
                from rd1 import parse
                if aware(receipt.received_at) < parse(previous):
                    raise RiskError("Wallet source time regressed")
            if not prior:
                self.s["frames"][event] = {"signature":signature,"raw":raw,"parsed_hash":digest(frame)}
            self.s.update(current=event,state="ACCOUNT_CHECK_PENDING",stage_validated=True)
            self._checked_evidence = None
            self._persist()  # block entry before native wallet construction/update
            self.source.frame = frame; self.source.now = aware(now)
            if self.wallets is None:
                config = {"stake_currency":"USDT","dry_run_wallet":1000,"dry_run":False,
                    "runmode":RunMode.BACKTEST,"trading_mode":TradingMode(self.market),
                    "margin_mode":"isolated" if self.market=="futures" else "", "tradable_balance_ratio":.99}
                # False selects official source-wallet parsing in this private
                # object; source has no network/order transport and no bot config is changed.
                self.wallets = Wallets(config,self.source,is_backtest=True)
            else:
                self.wallets.update()
            return self.wallets

    def _check_native(self):
        if self.wallets is None or self.source.frame is None:
            raise RiskError("Native wallet has not been reconstructed after bootstrap/restart")
        if self.s.get("stage_validated") is not True:
            raise RiskError("Latest wallet observation failed validation")
        frame = self.source.frame
        if (digest(frame) != self.s["frames"][self.s["current"]]["parsed_hash"]
                or self.wallets._exchange is not self.source or self.wallets._config["dry_run"] is not False
                or digest(self.source.markets) != self._native_market_hash
                or self.source.balance_includes_unrealized_pnl() is not False):
            raise RiskError("Native wallet source binding or staged payload changed")
        if set(self.wallets.get_all_balances()) != set(frame["balances"]):
            raise RiskError("Native wallet currency inventory changed")
        for currency, values in frame["balances"].items():
            for field in ("free","used","total"):
                if abs(dec(decimal_text(getattr(self.wallets.get_all_balances()[currency],field)))-dec(decimal_text(values[field]))) > EPS:
                    raise RiskError("Native wallet amounts differ from staged source")
        expected = {p["symbol"]:p for p in frame["positions"]}
        if set(self.wallets.get_all_positions()) != set(expected):
            raise RiskError("Native wallet positions changed")
        for pair, item in expected.items():
            p = self.wallets.get_all_positions()[pair]
            size = self.source._contracts_to_amount(pair,item["contracts"])
            for actual,want in ((p.position,size),(p.collateral,item["initialMargin"]),(p.leverage,1),(p.unrealized_pnl,item["unrealizedPnl"])):
                if abs(dec(decimal_text(actual))-dec(decimal_text(want))) > EPS:
                    raise RiskError("Native parsed futures wallet differs from staged source")
            if p.side != "long":
                raise RiskError("Native wallet direction changed")

    def confirm(self, checked_evidence, now):
        with self._mutex:
            self._ready(now); self._check_native()
            key = str(checked_evidence.snapshot.cursor)
            checkpoint = self.provider.s["checkpoints"].get(key)
            current = self.s["frames"][self.s["current"]]
            if (checkpoint is None or checkpoint["output"] != primitive(asdict(checked_evidence))
                    or checkpoint["input"]["evidence"] != current["raw"]["evidence"]
                    or self.provider.s["last_cursor"] != checked_evidence.snapshot.cursor
                    or checkpoint["audit"]["event_sequence"] != self.provider.ledger.last_sequence):
                raise RiskError("Wallet has no matching latest complete account-provider checkpoint")
            self.provider.bridge.validate(checked_evidence,now)
            current.update(checkpoint_cursor=key,checkpoint_hash=digest(checkpoint),native_refresh_at=stamp(self.wallets._last_wallet_refresh))
            self.s["state"] = "VERIFIED"
            self._persist()
            self._checked_evidence = copy.deepcopy(checked_evidence)

    def require_entry_clear(self, now):
        with self._mutex:
            self._ready(now)
            if self.s["state"] != "VERIFIED":
                raise RiskError("Wallet/account bootstrap or reconciliation is pending")
            self._check_native()
            if self._checked_evidence is None:
                raise RiskError("Wallet bootstrap needs a fresh complete account confirmation")
            self.provider.bridge.validate(self._checked_evidence,now)
            self.provider._native_events()
            frame = self.s["frames"][self.s["current"]]
            checkpoint = self.provider.s["checkpoints"].get(frame["checkpoint_cursor"])
            if (checkpoint is None or digest(checkpoint) != frame["checkpoint_hash"]
                    or str(self.provider.s["last_cursor"]) != frame["checkpoint_cursor"]
                    or checkpoint["audit"]["event_sequence"] != self.provider.ledger.last_sequence):
                raise RiskError("Wallet account event watermark changed")
            from rd1 import parse
            if not 0 <= (aware(now)-parse(frame["raw"]["receipt"]["received_at"])).total_seconds() <= 15:
                raise RiskError("Verified wallet source receipt is stale")
            if self.wallets._last_wallet_refresh != aware(now) or self.source.now != aware(now):
                raise RiskError("Wallet needs explicit refresh at the current replay event time")
