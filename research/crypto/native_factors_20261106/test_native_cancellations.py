"""Native cancel/query builders and terminal handler; synthetic tape, real SQLite."""
import copy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import ccxt
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE/"sources"))
from native_cancellations import LocalCancelExchange, NativeCancellation
from native_entries import LocalBinanceTape
from native_lifecycle import NativeLifecycle
from native_evidence import CapturedOrder
from rd1 import RiskError, StateError
from freqtrade.persistence import Trade, Order
from freqtrade.exceptions import TemporaryError
import test_native_entries as ef
import test_native_after_fill as af
import test_native_lifecycle as lf

T = ef.T


class CancellationTests(unittest.TestCase):
    setUp = ef.NativeEntryTests.setUp
    tearDown = ef.NativeEntryTests.tearDown
    account = lf.NativeReconciliationTests.account
    fill = lf.NativeReconciliationTests.fill

    def harness(self, market="spot", filled=0, prior=0, status="CANCELED", seconds=60):
        with patch("test_native_entries.Lifecycle", NativeLifecycle):
            life, gate, ex, bot, guard, trade, _, e, p = af.AfterFillTests.harness(self, market)
        gate.bind_native("r1")
        raw = copy.deepcopy(gate.s["requests"]["r1"]["ccxt_payload"])
        now = T+timedelta(seconds=seconds)
        if prior:
            raw.update(filled=float(prior), remaining=5-float(prior), average=10.,cost=float(prior)*10)
            trade.orders[0].update_from_ccxt_object(raw); Trade.commit()
        report = CapturedOrder(json.dumps(raw), now, "synthetic-fresh-open-order", "synthetic")
        evidence = self.account(report, trade, str(prior), str(1000-prior*10), cursor=2, market=market)
        terminal = copy.deepcopy(gate.s["requests"]["r1"]["venue_payload"])
        terminal.update(status=status, executedQty=str(filled), cummulativeQuoteQty=str(filled*10),
                        cumQuote=str(filled*10), avgPrice="10" if filled else "0", time=int(T.timestamp()*1000),
                        updateTime=int((now+timedelta(seconds=1)).timestamp()*1000))
        delete = copy.deepcopy(terminal)
        if market == "spot":
            delete.update(origClientOrderId=terminal["clientOrderId"], clientOrderId="synthetic-cancel-alias")
            terminal["clientOrderId"] = "synthetic-cancel-alias"
        received = now+timedelta(seconds=1)
        tape = LocalBinanceTape([ef.market_fixture(market)], [(delete,received),(terminal,received)], allowed_methods=("GET","DELETE"))
        cancel_ex = LocalCancelExchange(tape, market); self.opened.append(cancel_ex)
        control = NativeCancellation(self.root/"cancellation", gate, create=True); self.opened.append(control)
        return life,gate,bot,guard,trade,evidence,cancel_ex,control,now,received

    def test_spot_zero_cancel_uses_real_delete_get_and_native_cleanup_before_capacity_release(self):
        life,gate,bot,guard,trade,e,ex,c,now,received = self.harness()
        report = c.dispatch("r1",e,ex,now)
        self.assertEqual([r["method"] for r in ex._api.requests],["DELETE","GET"])
        self.assertEqual(ex._api.requests[0]["params"]["orderId"],"100")
        self.assertEqual(json.loads(report.payload_json)["clientOrderId"],gate.s["requests"]["r1"]["ticket"]["client_order_id"])
        self.assertEqual(c.s["requests"]["r1"]["result"]["clientOrderId"],"synthetic-cancel-alias")
        report = c.apply_terminal("r1",bot,received,fills=(),fills_complete=True)
        self.assertEqual(Trade.get_trades_proxy(),[])
        self.assertEqual(life.s["requests"]["r1"]["lifecycle"]["state"],"SUBMISSION_UNKNOWN")
        account = self.account(report,cursor=3)
        result = c.reconcile("r1",account,received,fills=())
        self.assertEqual(result["state"],"TERMINAL_RECONCILED")
        self.assertEqual(c.reconcile("r1",account,received,fills=()),result)

    def test_futures_partial_cancel_keeps_race_fill_and_native_protection(self):
        life,gate,bot,guard,trade,e,ex,c,now,received = self.harness("futures",filled=3,prior=2)
        c.dispatch("r1",e,ex,now)
        fills=(self.fill(quantity="2",quote=".02",seconds=30,market="futures"),
               self.fill(fill_id="f2",quantity="1",quote=".01",sequence=2,seconds=61,market="futures"))
        report=c.apply_terminal("r1",bot,received,fills=fills,fills_complete=True)
        self.assertEqual(trade.amount,3)
        self.assertEqual(trade.stop_loss,9.5)
        self.assertFalse(c.s["requests"]["r1"]["native_fully_canceled"])
        self.assertEqual(trade.fee_open_cost,.03)
        guard.require_entry_clear()
        account=self.account(report,trade,"3","999.97",cursor=3,market="futures")
        result=c.reconcile("r1",account,received,fills=fills)
        self.assertEqual(result["native_campaign"]["net_remaining_quantity"],"3")
        self.assertEqual(result["state"],"TERMINAL_RECONCILED")

    def test_native_fabricated_cancel_fallback_is_preserved_as_failure_not_terminal(self):
        life,gate,bot,guard,trade,e,ex,c,now,received=self.harness()
        ex._api.responses=[ccxt.InvalidOrder("synthetic cancel missing"),ccxt.InvalidOrder("synthetic query missing")]
        with self.assertRaisesRegex(RiskError,"fallback"):
            c.dispatch("r1",e,ex,now)
        self.assertEqual(len(ex._api.requests),2)
        self.assertEqual(c.s["requests"]["r1"]["state"],"DISPATCH_UNKNOWN")
        self.assertTrue(trade.orders[0].ft_is_open)
        self.assertEqual(life.s["requests"]["r1"]["lifecycle"]["state"],"SUBMISSION_UNKNOWN")

    def test_timeout_is_single_attempt_and_restart_does_not_redispatch(self):
        _,gate,_,_,trade,e,ex,c,now,_=self.harness()
        ex._api.responses=[ccxt.RequestTimeout("synthetic lost response")]
        with self.assertRaises(TemporaryError): c.dispatch("r1",e,ex,now)
        self.assertEqual(len(ex._api.requests),1)
        c.close(); c=NativeCancellation(self.root/"cancellation",gate); self.opened.append(c)
        with self.assertRaisesRegex(StateError,"already attempted"): c.dispatch("r1",e,ex,now)
        self.assertEqual(len(ex._api.requests),1)

    def test_missing_balance_does_not_block_safe_cancellation_but_does_block_new_entry(self):
        _,gate,_,_,_,e,ex,c,now,_=self.harness()
        bad=replace(e,balance_complete=False,snapshot=replace(e.snapshot,balance_at=T,quotes=()))
        with self.assertRaisesRegex(RiskError,"Incomplete"): gate.bridge.validate(bad,now)
        c.dispatch("r1",bad,ex,now)
        self.assertEqual(len(ex._api.requests),2)

    def test_frozen_sixty_second_timeout_not_shortened(self):
        _,_,_,_,_,e,ex,c,now,_=self.harness(seconds=59)
        with self.assertRaisesRegex(RiskError,"no cancellation action"): c.dispatch("r1",e,ex,now)
        self.assertEqual(ex._api.requests,[])

    def test_wrong_alias_and_wrong_order_never_reach_native_cleanup(self):
        _,_,_,_,trade,e,ex,c,now,_=self.harness()
        ex._api.responses[0][0]["origClientOrderId"]="unrelated"
        with self.assertRaisesRegex(RiskError,"original link"): c.dispatch("r1",e,ex,now)
        self.assertTrue(trade.orders[0].ft_is_open)

    def test_missing_race_fill_and_missing_fee_reject_before_native_mutation(self):
        _,_,bot,_,trade,e,ex,c,now,received=self.harness(filled=3,prior=2)
        c.dispatch("r1",e,ex,now)
        with self.assertRaisesRegex(RiskError,"misses cumulative"):
            c.apply_terminal("r1",bot,received,fills=(self.fill(quantity="2",quote=".02",seconds=30),),fills_complete=True)
        with self.assertRaisesRegex(RiskError,"positive quote fee"):
            c.apply_terminal("r1",bot,received,fills=(self.fill(quantity="3",seconds=61),),fills_complete=True)
        self.assertEqual(trade.amount,0)
        self.assertEqual(trade.orders[0].filled,2)

    def test_open_response_retains_native_order_and_reservation(self):
        life,_,bot,_,trade,e,ex,c,now,received=self.harness(status="NEW")
        c.dispatch("r1",e,ex,now)
        with self.assertRaisesRegex(RiskError,"no terminal"): c.apply_terminal("r1",bot,received,fills=(),fills_complete=True)
        self.assertTrue(trade.orders[0].ft_is_open)
        self.assertEqual(life.s["requests"]["r1"]["lifecycle"]["state"],"SUBMISSION_UNKNOWN")

    def test_cancel_persist_failure_never_reaches_transport(self):
        _,_,_,_,_,e,ex,c,now,_=self.harness()
        with patch.object(c.store,"save",side_effect=OSError("synthetic disk failure")):
            with self.assertRaisesRegex(StateError,"not acknowledged"): c.dispatch("r1",e,ex,now)
        self.assertEqual(ex._api.requests,[])

    def test_native_dust_refusal_preserved(self):
        _,_,_,_,_,e,ex,c,now,_=self.harness(prior=.1,filled=.1)
        with self.assertRaisesRegex(RiskError,"unexitable partial"): c.dispatch("r1",e,ex,now)
        self.assertEqual(ex._api.requests,[])

    def test_incomplete_orders_or_stale_receipt_cannot_cancel(self):
        _,_,_,_,_,e,ex,c,now,_=self.harness()
        with self.assertRaisesRegex(RiskError,"Incomplete"):
            c.dispatch("r1",replace(e,orders_complete=False),ex,now)
        with self.assertRaisesRegex(RiskError,"stale"):
            c.dispatch("r1",replace(e,order_envelopes=(replace(e.order_envelopes[0],received_at=T),)),ex,now)
        self.assertEqual(ex._api.requests,[])

    def test_timeout_restart_recovers_by_query_only_and_preserves_full_fill(self):
        life,gate,bot,guard,trade,e,ex,c,now,received=self.harness("futures",filled=5,status="FILLED")
        response=copy.deepcopy(ex._api.responses[-1])
        ex._api.responses=[ccxt.RequestTimeout("synthetic lost delete reply")]
        with self.assertRaises(TemporaryError): c.dispatch("r1",e,ex,now)
        c.close(); c=NativeCancellation(self.root/"cancellation",gate); self.opened.append(c)
        ex._api.responses=[response]
        c.query("r1",ex,received)
        fills=(self.fill(quantity="5",quote=".05",seconds=61,market="futures"),)
        report=c.apply_terminal("r1",bot,received,fills=fills,fills_complete=True)
        self.assertEqual(trade.amount,5)
        self.assertEqual([r["method"] for r in ex._api.requests],["DELETE","GET"])
        account=self.account(report,trade,"5","999.95",cursor=3,market="futures")
        self.assertEqual(c.reconcile("r1",account,received,fills=fills)["state"],"TERMINAL_RECONCILED")

    def test_partial_native_commit_ack_loss_recovers_without_repeating_callback(self):
        _,gate,bot,guard,trade,e,ex,c,now,received=self.harness(filled=3)
        c.dispatch("r1",e,ex,now)
        fills=(self.fill(quantity="3",quote=".03",seconds=61),)
        commit=Trade.commit
        def commit_then_fail():
            commit()
            if any(x["stages"].get("after_fill_hook") for x in guard.s["events"].values()):
                raise RuntimeError("synthetic native acknowledgement lost")
        with patch.object(Trade,"commit",side_effect=commit_then_fail):
            with self.assertRaisesRegex(RuntimeError,"acknowledgement lost"):
                c.apply_terminal("r1",bot,received,fills=fills,fills_complete=True)
        Trade.session.remove()
        c.close(); c=NativeCancellation(self.root/"cancellation",gate); self.opened.append(c)
        with patch.object(bot.strategy,"order_filled",side_effect=AssertionError("callback must not repeat")):
            c.recover_applied("r1")
        self.assertEqual(Trade.session.get(Trade,c.s["requests"]["r1"]["trade_id"]).amount,3)
        self.assertEqual(c.s["requests"]["r1"]["state"],"NATIVE_APPLIED_ACCOUNT_PENDING")
        self.assertEqual(len(ex._api.requests),2)

    def test_zero_cleanup_commit_ack_loss_recovers_only_if_rows_are_gone(self):
        _,gate,bot,_,trade,e,ex,c,now,received=self.harness()
        c.dispatch("r1",e,ex,now)
        delete=Trade.delete
        def delete_then_fail(row):
            delete(row)
            raise RuntimeError("synthetic cleanup acknowledgement lost")
        with patch.object(Trade,"delete",delete_then_fail):
            with self.assertRaisesRegex(RuntimeError,"cleanup acknowledgement"):
                c.apply_terminal("r1",bot,received,fills=(),fills_complete=True)
        Trade.session.remove()
        c.recover_applied("r1")
        self.assertEqual(Trade.get_trades_proxy(),[])
        self.assertEqual(c.s["requests"]["r1"]["state"],"NATIVE_APPLIED_ACCOUNT_PENDING")

    def test_native_precommit_failure_cannot_recover_or_redispatch(self):
        _,_,bot,_,trade,e,ex,c,now,received=self.harness()
        c.dispatch("r1",e,ex,now)
        with patch.object(Trade,"delete",side_effect=RuntimeError("synthetic cleanup failed")):
            with self.assertRaises(RuntimeError): c.apply_terminal("r1",bot,received,fills=(),fills_complete=True)
        Trade.session.rollback()
        with self.assertRaisesRegex(RiskError,"not committed"): c.recover_applied("r1")
        with self.assertRaisesRegex(StateError,"already attempted"): c.dispatch("r1",e,ex,now)
        self.assertEqual(len(ex._api.requests),2)

    def test_query_failure_cannot_reuse_stale_success_and_has_no_hidden_retry(self):
        _,_,_,_,_,e,ex,c,now,received=self.harness(status="NEW")
        c.dispatch("r1",e,ex,now)
        ex._api.responses=[ccxt.RequestTimeout("synthetic lost query reply")]
        with self.assertRaises(TemporaryError): c.query("r1",ex,received+timedelta(seconds=1))
        self.assertEqual([r["method"] for r in ex._api.requests],["DELETE","GET","GET"])
        self.assertIsNone(ex._api.last_response)

    def test_missing_account_inventory_keeps_capacity_after_native_success(self):
        life,_,bot,_,trade,e,ex,c,now,received=self.harness(filled=3)
        c.dispatch("r1",e,ex,now)
        fills=(self.fill(quantity="3",quote=".03",seconds=61),)
        report=c.apply_terminal("r1",bot,received,fills=fills,fills_complete=True)
        missing=self.account(report,cursor=3)
        with self.assertRaisesRegex(RiskError,"position inventories differ"):
            c.reconcile("r1",missing,received,fills=fills)
        self.assertEqual(life.s["requests"]["r1"]["lifecycle"]["state"],"SUBMISSION_UNKNOWN")

    def test_query_cannot_regress_fills_seen_on_delete_response(self):
        _,_,_,_,trade,e,ex,c,now,_=self.harness(filled=3)
        ex._api.responses[0][0].update(executedQty="4",cummulativeQuoteQty="40")
        with self.assertRaisesRegex(RiskError,"cumulative fills.*regressed"):
            c.dispatch("r1",e,ex,now)
        self.assertTrue(trade.orders[0].ft_is_open)

    def test_unrelated_query_order_is_rejected_without_native_mutation(self):
        _,_,_,_,trade,e,ex,c,now,_=self.harness()
        ex._api.responses[1][0]["orderId"]=101
        with self.assertRaisesRegex(RiskError,"transport identity"):
            c.dispatch("r1",e,ex,now)
        self.assertTrue(trade.orders[0].ft_is_open)

    def test_native_order_mutation_after_receipt_cannot_be_deleted_as_zero_fill(self):
        _,_,bot,_,trade,e,ex,c,now,received=self.harness()
        c.dispatch("r1",e,ex,now)
        trade.orders[0].amount=6.; Trade.commit()
        with self.assertRaisesRegex(RiskError,"initial entry changed"):
            c.apply_terminal("r1",bot,received,fills=(),fills_complete=True)
        self.assertEqual(len(Trade.get_trades_proxy()),1)
        self.assertTrue(trade.orders[0].ft_is_open)


if __name__ == "__main__":
    unittest.main()
