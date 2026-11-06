"""Actual native fee/update/after-fill/stop/liquidation code with offline inputs."""
import copy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import sys
from types import MethodType
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE/"sources"))
from native_after_fill import AfterFillGuard, OfflineProtectionExchange
from native_entries import NativeEntryGate
from native_evidence import CapturedOrder
from rd1 import RiskError, StateError
from freqtrade.freqtradebot import FreqtradeBot
from freqtrade.persistence import Trade, Order
import test_native_entries as entry_fixture
from test_native_factors import resolved
T = entry_fixture.T


class AfterFillTests(unittest.TestCase):
    setUp = entry_fixture.NativeEntryTests.setUp
    tearDown = entry_fixture.NativeEntryTests.tearDown

    def harness(self, market="spot", strategy="F1Momentum168Native", tiers=True):
        life, gate, ex, bot, evidence, proposal = entry_fixture.NativeEntryTests.harness(self, market)
        guard = AfterFillGuard(self.root/"protection", life.risk, create=True); self.opened.append(guard)
        gate.close()
        gate = NativeEntryGate(self.root/(market+"-receipts"), life, after_fill_guard=guard); self.opened.append(gate)
        ex.gate = gate
        entry_fixture.NativeEntryTests.execute(self, ex, bot, evidence, proposal)
        trade = Trade.get_trades_proxy()[0]
        table = {proposal.pair:[{"minNotional":0, "maxNotional":50000, "maintenanceMarginRate":.004, "maintAmt":0}]} if tiers else {}
        offline = OfflineProtectionExchange(market, [entry_fixture.market_fixture(market)], table, "synthetic-rule-table")
        self.opened.append(offline)
        bot.exchange = offline; bot.strategy = resolved(strategy, (proposal.pair,))
        bot.config["dry_run"] = True; bot.margin_mode = offline.margin_mode
        # Native fee handling and trade update are real; this wallet is synthetic.
        bot.wallets.get_free = Mock(return_value=0)
        bot.order_close_notify = Mock()  # notification/protection-manager boundary, no RPC
        for name in ("order_obj_or_raise", "handle_order_fee", "get_real_amount", "apply_fee_conditional", "cancel_stoploss_on_exchange"):
            setattr(bot, name, MethodType(getattr(FreqtradeBot, name), bot))
        raw = copy.deepcopy(gate.s["requests"]["r1"]["ccxt_payload"])
        raw.update(status="closed", filled=5., remaining=0., average=10., cost=50.,
                   fee={"currency":"USDT", "cost":.05, "rate":.001})
        envelope = CapturedOrder(json.dumps(raw), T+timedelta(seconds=1), "synthetic-fill", "synthetic")
        return life, gate, ex, bot, guard, trade, envelope, evidence, proposal

    def apply(self, guard, bot, trade, envelope, event="fill"):
        return guard.apply(event, bot, trade, envelope, envelope.received_at)

    def assert_gate_blocked(self, gate, ex, evidence, proposal):
        with self.assertRaisesRegex((RiskError, StateError), "protection|Protection|after-fill"):
            gate.submit(ex, "blocked", evidence, proposal, T, **gate.s["requests"]["r1"]["request"])
        self.assertEqual(len(ex._api.requests), 1)

    def successful(self, market, strategy):
        _, _, _, bot, guard, trade, envelope, _, _ = self.harness(market, strategy)
        state = self.apply(guard, bot, trade, envelope)
        self.assertEqual(state["state"], "VERIFIED")
        self.assertEqual(trade.amount, 5)
        self.assertAlmostEqual(trade.stop_loss, 9.5)
        self.assertEqual(trade.fee_open_currency, "USDT")
        self.assertAlmostEqual(trade.fee_open_cost, .05)
        self.assertEqual(state["stages"]["order_filled"]["state"], "RETURNED")
        if market == "futures": self.assertAlmostEqual(trade.liquidation_price, .5)
        else: self.assertIsNone(trade.liquidation_price)
        guard.require_entry_clear()

    def test_f1_spot_uses_real_native_stop_and_fee_update(self):
        self.successful("spot", "F1Momentum168Native")

    def test_f2_futures_uses_native_binance_liquidation_and_common_rd1_stop(self):
        self.successful("futures", "F2Volatility24Native")

    def test_control_uses_same_common_stop_without_changing_official_signals(self):
        self.successful("spot", "AverageRD1ControlNative")

    def test_first_fill_price_improvement_reanchors_stop_to_actual_entry(self):
        _, _, _, bot, guard, trade, envelope, _, _ = self.harness()
        self.assertEqual(trade.stop_loss, 9.5)
        raw = json.loads(envelope.payload_json); raw.update(average=9.,cost=45.)
        state = self.apply(guard, bot, trade, replace(envelope, payload_json=json.dumps(raw)))
        self.assertEqual(trade.open_rate, 9.)
        self.assertAlmostEqual(trade.stop_loss, 8.55)
        self.assertEqual(state["state"], "VERIFIED")

    def test_suppressed_strategy_exception_cannot_count_as_protection_success(self):
        _, gate, ex, bot, guard, trade, envelope, e, p = self.harness()
        with patch.object(bot.strategy, "order_filled", side_effect=ValueError("synthetic callback failure")):
            with self.assertRaisesRegex(RiskError, "stage failed or missing"):
                self.apply(guard, bot, trade, envelope)
        self.assertEqual(guard.s["events"]["fill"]["stages"]["order_filled"]["state"], "ERROR")
        self.assertEqual(trade.amount, 5)
        self.assert_gate_blocked(gate, ex, e, p)

    def test_missing_tiers_cannot_certify_a_stale_liquidation_value(self):
        _, gate, ex, bot, guard, trade, envelope, e, p = self.harness("futures", tiers=False)
        trade.liquidation_price = .1; Trade.commit()
        with self.assertRaisesRegex(RiskError, "Liquidation refresh failed"):
            self.apply(guard, bot, trade, envelope)
        self.assertEqual(trade.liquidation_price, .1)
        self.assertEqual(guard.s["events"]["fill"]["stages"]["liquidation"]["state"], "ERROR")
        self.assert_gate_blocked(gate, ex, e, p)

    def test_open_partial_remains_pending_until_terminal_receipt(self):
        _, gate, ex, bot, guard, trade, envelope, e, p = self.harness()
        raw = json.loads(envelope.payload_json); raw.update(status="open",filled=2.,remaining=3.,cost=20.)
        partial = replace(envelope,payload_json=json.dumps(raw))
        with self.assertRaisesRegex(RiskError, "Open partial fill"):
            self.apply(guard, bot, trade, partial, "partial")
        self.assertEqual(trade.amount, 0)
        self.assertEqual(trade.orders[0].filled, 2)
        self.assert_gate_blocked(gate, ex, e, p)
        terminal = replace(envelope, received_at=T+timedelta(seconds=2))
        self.assertEqual(self.apply(guard, bot, trade, terminal, "terminal")["state"], "VERIFIED")
        self.assertEqual(guard.s["events"]["partial"]["state"], "SUPERSEDED_PARTIAL")
        guard.require_entry_clear()

    def test_native_commit_ack_loss_recovers_without_repeating_callbacks(self):
        life, _, _, bot, guard, trade, envelope, _, _ = self.harness("futures")
        commit = Trade.commit
        def commit_then_fail():
            commit()
            if guard.s["events"]["fill"]["stages"].get("after_fill_hook"):
                raise RuntimeError("synthetic final native commit acknowledgement lost")
        with patch.object(bot.strategy, "order_filled", wraps=bot.strategy.order_filled) as callback:
            with patch.object(Trade,"commit",side_effect=commit_then_fail):
                with self.assertRaises(RuntimeError): self.apply(guard,bot,trade,envelope)
            Trade.session.remove(); guard.close()
            reopened = AfterFillGuard(self.root/"protection",life.risk); self.opened.append(reopened)
            self.assertEqual(reopened.verify_committed("fill")["state"],"VERIFIED")
            callback.assert_called_once()
            reopened.require_entry_clear()

    def test_pre_commit_failure_cannot_recover_uncommitted_protection_values(self):
        life, gate, ex, bot, guard, trade, envelope, e, p = self.harness("futures")
        commit = Trade.commit
        def fail_before_second():
            if guard.s["events"]["fill"]["stages"].get("after_fill_hook"):
                raise RuntimeError("synthetic protection commit failed")
            commit()
        with patch.object(Trade,"commit",side_effect=fail_before_second):
            with self.assertRaises(RuntimeError): self.apply(guard,bot,trade,envelope)
        guard.close(); Trade.session.remove()
        reopened=AfterFillGuard(self.root/"protection",life.risk); self.opened.append(reopened)
        with self.assertRaisesRegex(RiskError,"Committed native state differs"):
            reopened.verify_committed("fill")
        with self.assertRaisesRegex(RiskError,"Unresolved after-fill"):
            reopened.require_entry_clear()

    def test_later_stop_mutation_blocks_entry(self):
        _, gate, ex, bot, guard, trade, envelope, e, p = self.harness()
        self.apply(guard,bot,trade,envelope)
        trade.stop_loss = 8.; Trade.commit()
        with self.assertRaisesRegex(RiskError,"changed after verification"):
            guard.require_entry_clear()
        self.assert_gate_blocked(gate, ex, e, p)

    def test_fill_outside_guard_and_deleted_trade_cannot_clear_entry_barrier(self):
        _, gate, ex, bot, guard, trade, envelope, e, p = self.harness()
        raw = json.loads(envelope.payload_json)
        trade.orders[0].update_from_ccxt_object(raw)
        trade.update_trade(trade.orders[0]); Trade.commit()
        with self.assertRaisesRegex(RiskError,"lacks matching protection"):
            guard.require_entry_clear()
        self.assert_gate_blocked(gate, ex, e, p)
        self.apply(guard, bot, trade, envelope)
        Trade.session.delete(trade); Trade.commit()
        with self.assertRaisesRegex(RiskError,"disappeared"):
            guard.require_entry_clear()

    def test_invalid_fill_price_or_limit_is_rejected_before_native_mutation(self):
        _, _, _, bot, guard, trade, envelope, _, _ = self.harness()
        for average, message in ((-1., "actual average"), (11., "violates native limit")):
            raw = json.loads(envelope.payload_json); raw["average"] = average
            with self.assertRaisesRegex(RiskError, message):
                self.apply(guard,bot,trade,replace(envelope,payload_json=json.dumps(raw)))
        self.assertEqual(trade.amount,0)
        self.assertEqual(trade.orders[0].filled,0)
        self.assertEqual(guard.s["events"],{})

    def test_partial_and_full_exit_refresh_remaining_protection_without_widening_stop(self):
        _, _, _, bot, guard, trade, envelope, _, _ = self.harness("futures")
        self.apply(guard,bot,trade,envelope)
        for oid, quantity, seconds in (("partial-exit",2.,2),("full-exit",3.,3)):
            raw=dict(id=oid, clientOrderId="synthetic-"+oid, symbol=trade.pair, type="limit", side="sell",
                     status="open", amount=quantity, filled=0., remaining=quantity, price=10., average=None,
                     timestamp=int((T+timedelta(seconds=seconds)).timestamp()*1000))
            order=Order.parse_from_ccxt_object(raw,trade.pair,"sell"); trade.orders.append(order); Trade.commit()
            raw.update(status="closed",filled=quantity,remaining=0.,average=10.,cost=quantity*10,
                       fee={"currency":"USDT","cost":.01,"rate":.001})
            event=CapturedOrder(json.dumps(raw),T+timedelta(seconds=seconds),"synthetic-exit-fill","synthetic")
            state=self.apply(guard,bot,trade,event,oid)
            self.assertEqual(state["state"],"VERIFIED")
            self.assertEqual(trade.stop_loss,9.5)
            guard.require_entry_clear()
        self.assertFalse(trade.is_open)


if __name__ == "__main__": unittest.main(verbosity=2)
