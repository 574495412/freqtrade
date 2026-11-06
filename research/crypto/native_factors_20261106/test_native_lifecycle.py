"""Native DB reconciliation with complete, explicitly synthetic fill histories."""
import copy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "sources"))
from native_lifecycle import NativeLifecycle, CampaignFill, scoped_order_id
from native_entries import NativeEntryGate
from native_snapshot import AccountEvidence
from native_evidence import CapturedOrder
from rd1 import Position, Order as RiskOrder, RiskError, StateError
from freqtrade.persistence import Trade, Order
import test_native_entries as entry_fixture
from test_native_entries import T, snapshot, entry, pair


class NativeReconciliationTests(unittest.TestCase):
    setUp = entry_fixture.NativeEntryTests.setUp
    tearDown = entry_fixture.NativeEntryTests.tearDown
    execute = entry_fixture.NativeEntryTests.execute

    def harness(self, *args, **kwargs):
        with patch("test_native_entries.Lifecycle", NativeLifecycle):
            return entry_fixture.NativeEntryTests.harness(self, *args, **kwargs)

    def fill(self, order_id="100", fill_id="f1", quantity="5", base="0", quote="0", side="buy", sequence=1, seconds=1, market="spot"):
        at = T+timedelta(seconds=seconds)
        return CampaignFill(order_id, fill_id, pair(market), side, quantity, "10", base, quote,
                            at, at, sequence, "synthetic-complete-fill-tape", "synthetic")

    def order_event(self, gate, status="closed", filled=5, base="0", seconds=1):
        raw = copy.deepcopy(gate.s["requests"]["r1"]["ccxt_payload"])
        raw.update(status=status, filled=float(filled), remaining=5-float(filled), average=10.0, cost=float(filled)*10)
        rows = Trade.session.query(Order).filter(Order.ft_pair == raw["symbol"], Order.order_id == raw["id"]).all()
        trade = None
        if rows:
            order = rows[0]; order.update_from_ccxt_object(raw); order.ft_fee_base = float(base)
            trade = Trade.session.get(Trade, order.ft_trade_id)
            trade.update_trade(order); Trade.commit()
        return trade, CapturedOrder(json.dumps(raw), T+timedelta(seconds=seconds), "synthetic-order-receipt", "synthetic")

    def account(self, envelope, trade=None, quantity="0", balance="1000", cursor=2, market="spot", partial_base="0"):
        raw = json.loads(envelope.payload_json); now = envelope.received_at
        positions = (); bindings = {}; orders = (); order_bindings = {}; envelopes = (); fees = {}
        if D(quantity):
            pos = Position("campaign-"+str(trade.id), pair(market), D(quantity), D(10), D(0))
            positions = (pos,); bindings = {pos.position_id: trade.id}
        if raw["status"] == "open":
            pending = RiskOrder("source-entry", pair(market), D(str(raw["remaining"])), D(10), T, True)
            orders = (pending,); order_bindings = {pending.order_id:(pair(market), raw["id"])}
            envelopes = (envelope,)
            if raw["filled"]: fees[pending.order_id] = partial_base
        snap = snapshot(market, at=now, cursor=cursor, balance=balance, positions=positions, orders=orders)
        return AccountEvidence("synthetic-entry", market, "synthetic-complete-account", "synthetic", snap,
                               True, True, True, bindings, order_bindings, envelopes, fees)

    def reconcile(self, gate, envelope, evidence, fills=(), event="e1"):
        return gate.reconcile_native("r1", event, envelope, evidence, envelope.received_at,
                                     fills=tuple(fills), fills_complete=True, sequence_basis="source_ordered")

    def test_zero_rejection_releases_reservation_and_exact_replay_is_idempotent(self):
        life, gate, ex, bot, initial, proposal = self.harness(response="REJECTED")
        self.assertFalse(self.execute(ex, bot, initial, proposal))
        _, report = self.order_event(gate, status="rejected", filled=0)
        account = self.account(report)
        with self.assertRaisesRegex(RiskError, "cannot be upgraded"):
            self.reconcile(gate, replace(report, evidence_class="captured"), replace(account, evidence_class="captured"))
        first = self.reconcile(gate, report, account)
        self.assertEqual(first["state"], "TERMINAL_RECONCILED")
        self.assertEqual(self.reconcile(gate, report, account), first)
        self.assertTrue(gate.bridge.reserve("r2", account, proposal, report.received_at)["reserved"])
        self.assertEqual(len(ex._api.requests), 1)

    def test_base_fee_and_partial_exit_reconcile_net_inventory_not_gross_fills(self):
        life, gate, ex, bot, initial, proposal = self.harness()
        self.execute(ex, bot, initial, proposal)
        trade, report = self.order_event(gate, base=".01", seconds=2)
        raw_exit = dict(id="exit", symbol=trade.pair, side="sell", type="limit", status="closed",
                        amount=2., filled=2., remaining=0., price=10., average=10., timestamp=int((T+timedelta(seconds=2)).timestamp()*1000))
        exit_order = Order.parse_from_ccxt_object(raw_exit, trade.pair, "sell")
        exit_order.ft_fee_base = 0; trade.orders.append(exit_order)
        trade.update_trade(exit_order); Trade.commit()
        self.assertEqual(D(str(trade.amount)), D("2.99"))
        account = self.account(report, trade, "2.99", "969.98")
        fills = (self.fill(base=".01"), self.fill("exit", "f2", "2", quote=".02", side="sell", sequence=2, seconds=2))
        state = self.reconcile(gate, report, account, fills)
        self.assertEqual(state["native_campaign"]["entry_gross_filled"], "5")
        self.assertEqual(state["native_campaign"]["net_remaining_quantity"], "2.99")
        self.assertEqual(state["native_campaign"]["spot_trade_cash_delta"], "-30.02")
        self.assertEqual(life.risk.equity(account.snapshot), D("999.88"))
        self.assertFalse(gate.bridge.reserve("again", account, proposal, report.received_at)["reserved"])

    def test_open_partial_then_cancellation_preserves_fill_history_and_no_double_reservation(self):
        life, gate, ex, bot, initial, proposal = self.harness()
        self.execute(ex, bot, initial, proposal)
        trade, report = self.order_event(gate, "open", 2, ".01")
        account = self.account(report, trade, "1.99", "980", partial_base=".01")
        fills = (self.fill(quantity="2", base=".01"),)
        self.assertEqual(self.reconcile(gate, report, account, fills)["state"], "OBSERVED_OPEN")
        # These three reservations fit the unchanged 30% cluster cap only when
        # the original BTC reservation is replaced by observed fill+remainder.
        rejected = gate.bridge.reserve("cost-boundary", account, entry(pair=pair("spot", "ETH"), quantity=D("9.9")), report.received_at)
        self.assertFalse(rejected["reserved"])
        self.assertIn("trade_risk_including_roundtrip_cost", rejected["reasons"])
        for asset, quantity in (("ETH", "9"), ("SOL", "9"), ("ADA", "6")):
            p = entry(pair=pair("spot", asset), quantity=D(quantity))
            result = gate.bridge.reserve(asset, account, p, report.received_at)
            self.assertTrue(result["reserved"], result)
        trade, canceled = self.order_event(gate, "canceled", 2, ".01", seconds=2)
        closed_account = self.account(canceled, trade, "1.99", "980", cursor=3)
        final = self.reconcile(gate, canceled, closed_account, fills, event="cancel")
        self.assertEqual(final["state"], "TERMINAL_RECONCILED")
        self.assertEqual(final["native_campaign"]["net_remaining_quantity"], "1.99")

    def test_same_venue_id_on_two_pairs_is_independent_but_pair_reuse_is_rejected(self):
        life, gate, ex, bot, initial, proposal = self.harness(response="REJECTED")
        self.execute(ex, bot, initial, proposal)
        _, btc = self.order_event(gate, "rejected", 0)
        account = self.account(btc)
        self.reconcile(gate, btc, account)
        eth_proposal = entry(pair=pair("spot", "ETH"))
        self.assertTrue(life.reserve("eth", account.snapshot, eth_proposal, btc.received_at)["reserved"])
        ticket = life.claim_for_replay("eth", account.snapshot, btc.received_at)
        raw = json.loads(btc.payload_json)
        raw.update(symbol=eth_proposal.pair, clientOrderId=ticket["client_order_id"], timestamp=int(btc.received_at.timestamp()*1000))
        eth = replace(btc, payload_json=json.dumps(raw), received_at=T+timedelta(seconds=2))
        e2 = self.account(eth, cursor=3)
        result = life.reconcile_native("eth", "eth-zero", eth, e2, eth.received_at, fills=(), fills_complete=True, sequence_basis="source_ordered")
        self.assertEqual(result["order_id"], scoped_order_id(eth_proposal.pair, "100"))
        self.assertTrue(life.reserve("btc-again", e2.snapshot, proposal, eth.received_at)["reserved"])
        ticket2 = life.claim_for_replay("btc-again", e2.snapshot, eth.received_at)
        raw.update(symbol=proposal.pair, clientOrderId=ticket2["client_order_id"], timestamp=int(eth.received_at.timestamp()*1000))
        reused = replace(eth, payload_json=json.dumps(raw), received_at=T+timedelta(seconds=3))
        with self.assertRaisesRegex(RiskError, "already bound"):
            life.reconcile_native("btc-again", "bad", reused, self.account(reused, cursor=4), reused.received_at,
                                  fills=(), fills_complete=True, sequence_basis="source_ordered")

    def test_missing_or_changed_fill_evidence_does_not_release_capacity(self):
        life, gate, ex, bot, initial, proposal = self.harness()
        self.execute(ex, bot, initial, proposal)
        trade, report = self.order_event(gate)
        account = self.account(report, trade, "5", "950")
        with self.assertRaisesRegex(RiskError, "Complete fill ledger"):
            self.reconcile(gate, report, account)
        self.assertEqual(life.s["requests"]["r1"]["lifecycle"]["state"], "SUBMISSION_UNKNOWN")
        with self.assertRaisesRegex(StateError, "Conflicting native receipt"):
            self.reconcile(gate, report, account, (self.fill(),))
        self.assertEqual(self.reconcile(gate, report, account, (self.fill(),), event="complete")["state"], "TERMINAL_RECONCILED")

    def test_unknown_completeness_sequence_and_fee_are_rejected(self):
        life, gate, ex, bot, initial, proposal = self.harness()
        self.execute(ex, bot, initial, proposal)
        trade, report = self.order_event(gate, base=".01")
        account = self.account(report, trade, "4.99", "950")
        for complete, sequence in ((False, "source_ordered"), (True, "unknown")):
            with self.assertRaisesRegex(RiskError, "Complete explicitly ordered"):
                life.reconcile_native("r1", "bad", report, account, report.received_at,
                    fills=(self.fill(base=".01"),), fills_complete=complete, sequence_basis=sequence)
        with self.assertRaisesRegex(RiskError, "applied base fee"):
            self.reconcile(gate, report, account, (self.fill(),))
        with self.assertRaisesRegex(RiskError, "Incomplete declared campaign sequence"):
            self.reconcile(gate, report, account, (self.fill(base=".01", sequence=2),), event="gap")
        self.assertEqual(life.s["requests"]["r1"]["lifecycle"]["state"], "SUBMISSION_UNKNOWN")

    def test_missing_native_order_with_nonzero_fill_is_not_a_rejection(self):
        life, gate, ex, bot, initial, proposal = self.harness(response="REJECTED")
        self.execute(ex, bot, initial, proposal)
        _, report = self.order_event(gate, "canceled", 2)
        with self.assertRaisesRegex(RiskError, "missing or ambiguous"):
            self.reconcile(gate, report, self.account(report), (self.fill(quantity="2"),))

    def test_full_exit_uses_zero_exposure_despite_closed_native_historical_amount(self):
        life, gate, ex, bot, initial, proposal = self.harness()
        self.execute(ex, bot, initial, proposal)
        trade, report = self.order_event(gate, seconds=2)
        raw = dict(id="exit-all", symbol=trade.pair, side="sell", type="limit", status="closed",
                   amount=5., filled=5., remaining=0., price=10., average=10., timestamp=int(report.received_at.timestamp()*1000))
        order = Order.parse_from_ccxt_object(raw, trade.pair, "sell"); order.ft_fee_base = 0
        trade.orders.append(order); trade.update_trade(order); Trade.commit()
        self.assertFalse(trade.is_open); self.assertGreater(trade.amount, 0)
        fills = (self.fill(), self.fill("exit-all", "f2", "5", side="sell", sequence=2, seconds=2))
        account = self.account(report)
        state = self.reconcile(gate, report, account, fills)
        self.assertEqual(state["native_campaign"]["net_remaining_quantity"], "0")
        self.assertTrue(gate.bridge.reserve("new-campaign", account, proposal, report.received_at)["reserved"])

    def test_lifecycle_commit_ack_loss_recovers_without_double_release_or_request(self):
        life, gate, ex, bot, initial, proposal = self.harness(response="REJECTED")
        self.execute(ex, bot, initial, proposal)
        _, report = self.order_event(gate, "rejected", 0); account = self.account(report)
        save = life.store.save
        def commit_then_fail(state):
            save(state)
            raise OSError("synthetic lifecycle acknowledgement lost")
        with patch.object(life.store, "save", side_effect=commit_then_fail):
            with self.assertRaisesRegex(StateError, "not durably acknowledged"):
                self.reconcile(gate, report, account)
        with self.assertRaisesRegex(StateError, "unavailable"):
            gate.bridge.reserve("next", account, proposal, report.received_at)
        gate.close(); life.close()
        restarted = NativeLifecycle(self.root/"spot", "synthetic-entry", "spot"); self.opened.append(restarted)
        recovered = NativeEntryGate(self.root/"spot-receipts", restarted); self.opened.append(recovered)
        self.assertEqual(self.reconcile(recovered, report, account)["state"], "TERMINAL_RECONCILED")
        self.assertTrue(recovered.bridge.reserve("next", account, proposal, report.received_at)["reserved"])
        self.assertEqual(len(ex._api.requests), 1)

    def test_cumulative_fill_history_cannot_be_rewritten_before_terminal_release(self):
        life, gate, ex, bot, initial, proposal = self.harness()
        self.execute(ex, bot, initial, proposal)
        trade, report = self.order_event(gate, "open", 2)
        first = self.fill(quantity="2")
        self.reconcile(gate, report, self.account(report, trade, "2", "980"), (first,))
        trade, terminal = self.order_event(gate, "canceled", 3, seconds=2)
        account = self.account(terminal, trade, "3", "970", cursor=3)
        with self.assertRaisesRegex(RiskError, "omitted or changed"):
            self.reconcile(gate, terminal, account, (replace(first, quantity="3"),), event="rewrite")
        self.assertEqual(life.s["requests"]["r1"]["lifecycle"]["state"], "OBSERVED_OPEN")
        second = self.fill(fill_id="f2", quantity="1", sequence=2, seconds=2)
        state = self.reconcile(gate, terminal, account, (first, second), event="complete")
        self.assertEqual(state["state"], "TERMINAL_RECONCILED")
        self.assertEqual(state["filled"], "3.0")

    def test_futures_quote_fees_and_duplicate_fill_identity(self):
        life, gate, ex, bot, initial, proposal = self.harness("futures")
        self.execute(ex, bot, initial, proposal)
        trade, report = self.order_event(gate)
        account = self.account(report, trade, "5", "999.95", market="futures")
        fill = self.fill(quote=".05", market="futures")
        with self.assertRaisesRegex(RiskError, "Conflicting duplicate campaign fill"):
            self.reconcile(gate, report, account, (fill, replace(fill, fee_quote=".06")), event="conflict")
        with self.assertRaisesRegex(RiskError, "unsupported settlement"):
            self.reconcile(gate, report, account, (replace(fill, fee_base=".01"),), event="base")
        state = self.reconcile(gate, report, account, (fill, fill), event="complete")
        self.assertEqual(len(state["native_campaign"]["fills"]), 1)
        self.assertEqual(state["native_campaign"]["quote_fees"], "0.05")
        self.assertEqual(life.risk.equity(account.snapshot), D("999.95"))

    def test_base_fee_on_exit_is_not_silently_given_native_subtraction_semantics(self):
        life, gate, ex, bot, initial, proposal = self.harness()
        self.execute(ex, bot, initial, proposal)
        trade, report = self.order_event(gate, seconds=2)
        raw = dict(id="exit", symbol=trade.pair, side="sell", type="limit", status="closed",
                   amount=2., filled=2., remaining=0., price=10., average=10., timestamp=int(report.received_at.timestamp()*1000))
        order = Order.parse_from_ccxt_object(raw, trade.pair, "sell"); order.ft_fee_base = .01
        trade.orders.append(order); trade.update_trade(order); Trade.commit()
        # Native amount subtraction yields 3.01; physical debit would yield 2.99.
        self.assertEqual(D(str(trade.amount)), D("3.01"))
        account = self.account(report, trade, "3.01", "970")
        fills = (self.fill(), self.fill("exit", "f2", "2", base=".01", side="sell", sequence=2, seconds=2))
        with self.assertRaisesRegex(RiskError, "unsupported settlement/dust"):
            self.reconcile(gate, report, account, fills)
        self.assertEqual(life.s["requests"]["r1"]["lifecycle"]["state"], "SUBMISSION_UNKNOWN")

    def test_generator_fill_inputs_are_not_consumed_by_evidence_logging(self):
        life, gate, ex, bot, initial, proposal = self.harness(response="REJECTED")
        self.execute(ex, bot, initial, proposal)
        _, report = self.order_event(gate, "rejected", 0); account = self.account(report)
        with self.assertRaisesRegex(RiskError, "outside the native campaign"):
            gate.reconcile_native("r1", "bad-generator", report, account, report.received_at,
                fills=(f for f in (self.fill(),)), fills_complete=True, sequence_basis="source_ordered")
        self.assertEqual(life.s["requests"]["r1"]["lifecycle"]["state"], "SUBMISSION_UNKNOWN")
        result = gate.reconcile_native("r1", "empty-generator", report, account, report.received_at,
            fills=(f for f in ()), fills_complete=True, sequence_basis="source_ordered")
        self.assertEqual(result["state"], "TERMINAL_RECONCILED")

    def test_terminal_release_does_not_clear_capital_floor_halt(self):
        life, gate, ex, bot, initial, proposal = self.harness(response="REJECTED")
        self.execute(ex, bot, initial, proposal)
        _, report = self.order_event(gate, "rejected", 0)
        # Explicit synthetic account loss; no claim this test reconciles its cause.
        account = self.account(report, balance="499")
        state = self.reconcile(gate, report, account)
        self.assertEqual(state["state"], "TERMINAL_RECONCILED")
        latches = copy.deepcopy(life.risk.s["latches"])
        self.assertTrue(latches)
        refused = gate.bridge.reserve("cannot-reenter", account, proposal, report.received_at)
        self.assertFalse(refused["reserved"])
        self.assertEqual(life.risk.s["latches"], latches)
        self.assertEqual(len(ex._api.requests), 1)


if __name__ == "__main__": unittest.main(verbosity=2)
