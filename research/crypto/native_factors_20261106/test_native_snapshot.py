"""Native SQLite inventory plus explicit synthetic RD1 account evidence."""
from dataclasses import replace
from decimal import Decimal as D
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "sources"))
from native_snapshot import AccountEvidence, NativeSnapshotBridge, CapturedOrder, OLD
sys.path.insert(0, str(OLD / "p0/risk"))
from test_rd1 import snapshot, entry, T, pair
from rd1 import Position, Order as RiskOrder, Funding, RiskError
from reservations import Reservations
from freqtrade.enums import TradingMode
from freqtrade.persistence import Trade, Order, init_db, CustomDataWrapper
from freqtrade.persistence.custom_data import _CustomData


class SnapshotBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="factor-snapshot-test-")
        self.root = Path(self.tmp.name)
        init_db("sqlite:///" + str(self.root / "isolated.sqlite"))
        Trade.use_db = True
        CustomDataWrapper.use_db = True
        self.market = "spot"
        self.reservations = Reservations(self.root / "spot", "synthetic-account", "spot", create=True)
        self.bridge = NativeSnapshotBridge(self.reservations)

    def tearDown(self):
        self.reservations.close()
        engine = Trade.session.get_bind()
        Trade.session.remove(); _CustomData.session.remove(); engine.dispose()
        self.tmp.cleanup()

    def evidence(self, snap=None, positions=None, orders=None, envelopes=(), fees=None):
        return AccountEvidence("synthetic-account", self.market, "synthetic-full-account", "synthetic",
            snap or snapshot(self.market), True, True, True, positions or {}, orders or {}, tuple(envelopes), fees or {})

    def make_trade(self, asset="BTC", quantity=5):
        p = pair(self.market, asset)
        t = Trade(pair=p, stake_currency="USDT", exchange="binance", amount=0,
                  stake_amount=quantity*10, amount_requested=quantity, open_rate=10,
                  is_open=True, open_date=T, fee_open=.0013, fee_close=.0013,
                  leverage=1, is_short=False, trading_mode=TradingMode(self.market))
        Trade.session.add(t)
        if quantity:
            self.add_order(t, "entry-"+asset, quantity, quantity, "buy", "closed")
        t.recalc_trade_from_orders(); Trade.commit()
        return t

    def add_order(self, trade, oid, amount, filled, side="buy", status="open", reduce_only=False):
        raw = dict(id=oid, clientOrderId="synthetic-"+oid, symbol=trade.pair, amount=amount,
                   filled=filled, remaining=amount-filled, side=side, type="limit", price=10,
                   average=10, status=status, timestamp=int(T.timestamp()*1000), reduceOnly=reduce_only)
        order = Order.parse_from_ccxt_object(raw, trade.pair, side, amount, 10)
        order.ft_fee_base = 0
        trade.orders.append(order)
        Trade.commit()
        return CapturedOrder(json.dumps(raw), T, "synthetic-orders", "synthetic"), order

    def position(self, trade, quantity):
        return Position("campaign-"+str(trade.id), trade.pair, D(str(quantity)), D(10), D(0))

    def test_flat_account_feeds_existing_atomic_reservations_without_order_permission(self):
        e = self.evidence()
        first = self.bridge.reserve("intent-1", e, entry(), T)
        again = self.bridge.reserve("intent-1", e, entry(), T)
        self.assertTrue(first["reserved"]); self.assertTrue(again["duplicate"])
        self.assertFalse(first["submission_authorized"])
        self.assertEqual(first["receipt"], again["receipt"])

    def test_database_inventory_prevents_omitting_or_inventing_positions(self):
        t = self.make_trade(); pos = self.position(t, 5)
        with self.assertRaisesRegex(RiskError, "position inventories"):
            self.bridge.validate(self.evidence(), T)
        snap = snapshot(balance="950", positions=(pos,))
        e = self.evidence(snap, {pos.position_id: t.id})
        self.assertIs(self.bridge.validate(e, T), snap)
        with self.assertRaisesRegex(RiskError, "position values"):
            self.bridge.validate(replace(e, snapshot=replace(snap, positions=(replace(pos, quantity=D(4)),))), T)

    def test_open_partial_entry_counts_fill_and_only_remaining_order_risk(self):
        t = self.make_trade(quantity=0)
        captured, order = self.add_order(t, "p", 5, 2)
        t.recalc_trade_from_orders(); Trade.commit()
        self.assertEqual(t.amount, 0)  # native core does not add still-open partial fills here
        pos = self.position(t, 2)
        pending = RiskOrder("source-p", t.pair, D(3), D(10), T, True)
        snap = snapshot(balance="980", positions=(pos,), orders=(pending,))
        e = self.evidence(snap, {pos.position_id:t.id}, {pending.order_id:(t.pair, "p")}, [captured], {pending.order_id:"0"})
        self.assertIs(self.bridge.validate(e, T), snap)
        self.assertEqual(self.reservations.risk.equity(snap), D(1000))
        with self.assertRaisesRegex(RiskError, "base fee is unknown"):
            self.bridge.validate(replace(e, partial_base_fees={}), T)
        with self.assertRaisesRegex(RiskError, "position values"):
            self.bridge.validate(replace(e, snapshot=replace(snap, positions=(replace(pos, quantity=D(4)),))), T)

    def test_open_partial_exit_reduces_effective_native_exposure(self):
        t = self.make_trade()
        captured, _ = self.add_order(t, "exit-p", 3, 1, "sell")
        self.assertEqual(t.amount, 5)
        pos = self.position(t, 4)
        pending = RiskOrder("source-exit", t.pair, D(-2), D(10), T, False, True)
        snap = snapshot(balance="960", positions=(pos,), orders=(pending,))
        e = self.evidence(snap, {pos.position_id:t.id}, {pending.order_id:(t.pair,"exit-p")}, [captured], {pending.order_id:"0"})
        self.assertIs(self.bridge.validate(e, T), snap)

    def test_venue_order_ids_are_scoped_by_pair_not_assumed_global(self):
        trades = [self.make_trade(asset=a, quantity=0) for a in ("BTC", "ETH")]
        pending=[]; captured=[]; bindings={}
        for t in trades:
            envelope, _ = self.add_order(t, "same-venue-id", 5, 0)
            oid = "source-"+t.pair
            pending.append(RiskOrder(oid, t.pair, D(5), D(10), T, True))
            captured.append(envelope); bindings[oid]=(t.pair,"same-venue-id")
        e = self.evidence(snapshot(orders=tuple(pending)), orders=bindings, envelopes=captured)
        self.assertIs(self.bridge.validate(e, T), e.snapshot)

    def test_mixed_partial_entry_exit_requires_fill_ordering(self):
        t = self.make_trade()
        captured_entry, _ = self.add_order(t, "add", 2, 1)
        captured_exit, _ = self.add_order(t, "reduce", 2, 1, "sell")
        pos = self.position(t, 5)
        orders = (RiskOrder("add", t.pair, D(1), D(10), T, True),
                  RiskOrder("reduce", t.pair, D(-1), D(10), T, False, True))
        e = self.evidence(snapshot(balance="950", positions=(pos,), orders=orders),
            {pos.position_id: t.id}, {o.order_id: (t.pair, o.order_id) for o in orders},
            [captured_entry, captured_exit], {o.order_id: "0" for o in orders})
        with self.assertRaisesRegex(RiskError, "complete fill ordering"):
            self.bridge.validate(e, T)

    def test_zero_net_partial_and_missing_average_are_not_usable_positions(self):
        t = self.make_trade(quantity=0)
        envelope, order = self.add_order(t, "partial", 2, 1)
        pos = self.position(t, 1)
        pending = RiskOrder("partial", t.pair, D(1), D(10), T, True)
        e = self.evidence(snapshot(balance="990", positions=(pos,), orders=(pending,)),
            {pos.position_id: t.id}, {"partial": (t.pair, "partial")}, [envelope], {"partial": "0"})
        raw = json.loads(envelope.payload_json); raw.pop("average")
        with self.assertRaisesRegex(RiskError, "actual average"):
            self.bridge.validate(replace(e, order_envelopes=(replace(envelope, payload_json=json.dumps(raw)),)), T)
        order.ft_fee_base = 1; Trade.commit()
        with self.assertRaisesRegex(RiskError, "base fee differs"):
            self.bridge.validate(replace(e, partial_base_fees={"partial": "1"}), T)

    def test_futures_exit_requires_original_reduce_only_evidence(self):
        self.reservations.close(); self.market = "futures"
        self.reservations = Reservations(self.root / "futures", "synthetic-account", "futures", create=True)
        self.bridge = NativeSnapshotBridge(self.reservations)
        t = self.make_trade(); pos = self.position(t, 5)
        envelope, _ = self.add_order(t, "protect", 2, 0, "sell", reduce_only=True)
        pending = RiskOrder("protect", t.pair, D(-2), D(10), T, False, True)
        e = self.evidence(snapshot("futures", positions=(pos,), orders=(pending,)),
            {pos.position_id: t.id}, {"protect": (t.pair, "protect")}, [envelope])
        self.assertIs(self.bridge.validate(e, T), e.snapshot)
        raw = json.loads(envelope.payload_json); raw.pop("reduceOnly")
        with self.assertRaisesRegex(RiskError, "captured reduceOnly"):
            self.bridge.validate(replace(e, order_envelopes=(replace(envelope, payload_json=json.dumps(raw)),)), T)

    def test_receipts_creation_and_completeness_never_inferred(self):
        t = self.make_trade(quantity=0); envelope, _ = self.add_order(t, "x", 5, 0)
        pending=RiskOrder("source-x", t.pair, D(5), D(10), T, True)
        e=self.evidence(snapshot(orders=(pending,)), orders={pending.order_id:(t.pair,"x")}, envelopes=[envelope])
        with self.assertRaisesRegex(RiskError, "Incomplete"):
            self.bridge.validate(replace(e, orders_complete=False), T)
        raw=json.loads(envelope.payload_json); raw.pop("timestamp")
        with self.assertRaisesRegex(RiskError, "creation timestamp"):
            self.bridge.validate(replace(e, order_envelopes=(replace(envelope,payload_json=json.dumps(raw)),)), T)
        with self.assertRaisesRegex(RiskError, "balance semantics"):
            self.bridge.validate(replace(e,snapshot=replace(e.snapshot,balance_semantics="free_cash")), T)

    def test_futures_funding_status_is_preserved_not_reconstructed_from_native_trade(self):
        self.reservations.close(); self.market="futures"
        self.reservations=Reservations(self.root/'futures', 'synthetic-account','futures',create=True)
        self.bridge=NativeSnapshotBridge(self.reservations)
        t=self.make_trade();pos=self.position(t,5)
        snap=snapshot('futures',positions=(pos,),funding=(Funding('f1',D(3),False),))
        e=self.evidence(snap,{pos.position_id:t.id})
        self.assertIs(self.bridge.validate(e,T),snap)
        self.assertEqual(self.reservations.risk.equity(snap),D(997))
        settled=replace(snap,balance=D(997),funding=(Funding('f1',D(3),True),))
        self.assertEqual(self.reservations.risk.equity(self.bridge.validate(replace(e,snapshot=settled),T)),D(997))
        with self.assertRaisesRegex(RiskError,'funding incomplete'):
            self.bridge.validate(replace(e,snapshot=replace(snap,funding_complete=False)),T)


if __name__ == '__main__':
    unittest.main(verbosity=2)
