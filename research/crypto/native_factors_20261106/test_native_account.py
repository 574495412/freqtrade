"""Whole-account source journal versus actual isolated native inventory and RD1."""
import copy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import json
from pathlib import Path
import sys
from types import MethodType
import unittest
from unittest.mock import patch, Mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE/"sources"))
from native_account import NativeAccountProvider, QuoteReceipt
from native_lifecycle import NativeLifecycle
from native_evidence import CapturedOrder
from native_snapshot import AccountEvidence
from native_after_fill import AfterFillGuard, OfflineProtectionExchange
from rd1 import Funding, RiskError, StateError, stamp
from freqtrade.persistence import Trade, Order
from freqtrade.freqtradebot import FreqtradeBot
from test_native_factors import resolved
import test_native_entries as ef
import test_native_lifecycle as lf

T = ef.T; MIDNIGHT = T.replace(hour=0)
SOURCE = "synthetic-complete-account-stream"


class NativeAccountTests(unittest.TestCase):
    setUp = ef.NativeEntryTests.setUp
    tearDown = ef.NativeEntryTests.tearDown
    execute = ef.NativeEntryTests.execute
    order_event = lf.NativeReconciliationTests.order_event

    def provider(self, life, *, create=True):
        provider = NativeAccountProvider(self.root/"account",life,source_id=SOURCE,evidence_class="synthetic",
            cost_scenario="base",inception_at=MIDNIGHT,create=create)
        self.opened.append(provider)
        return provider

    def harness(self, market="spot", *, initialize=True):
        with patch("test_native_entries.Lifecycle", NativeLifecycle):
            life,gate,ex,bot,e,p = ef.NativeEntryTests.harness(self,market)
        provider = self.provider(life)
        start = replace(e, source_id=SOURCE,snapshot=ef.snapshot(market,at=MIDNIGHT,cursor=0))
        if initialize:
            self.checkpoint(provider,start,sequence=0)
        return life,gate,ex,bot,e,p,provider,start

    def receipts(self, e):
        return tuple(QuoteReceipt(q,e.snapshot.at,"synthetic-explicit-quote-feed","synthetic") for q in e.snapshot.quotes)

    def checkpoint(self, provider, e, sequence=None, **kwargs):
        return provider.checkpoint(e,self.receipts(e),e.snapshot.at,complete_sequence=provider.ledger.last_sequence if sequence is None else sequence,
            complete_from_inception=True,funding_complete=True,**kwargs)

    def event(self, trade, sequence=1, quantity="5", fee=".05", seconds=1, **kwargs):
        at=T+timedelta(seconds=seconds)
        event=dict(event_id="e"+str(sequence),sequence=sequence,event_at=stamp(at),received_at=stamp(at),
            kind="fill",source_id=SOURCE,evidence_class="synthetic",account_id="synthetic-entry",market=trade.trading_mode.value,
            pair=trade.pair,campaign_id="campaign-"+str(trade.id),native_trade_id=trade.id,order_id="100",fill_id="f"+str(sequence),
            signed_quantity=quantity,price="10",fee_quote=fee,fee_base="0")
        event.update(kwargs)
        return event

    def account(self, report, trade=None, quantity="0", balance="1000", cursor=2, market="spot", **kwargs):
        evidence=lf.NativeReconciliationTests.account(self,report,trade,quantity,balance,cursor,market)
        return replace(evidence,source_id=SOURCE,snapshot=replace(evidence.snapshot,**kwargs))

    def filled(self, market="spot"):
        life,gate,ex,bot,e,p,provider,start=self.harness(market)
        self.execute(ex,bot,e,p)
        trade,report=self.order_event(gate)
        provider.append(self.event(trade))
        account=self.account(report,trade,"5","949.95" if market=="spot" else "999.95",market=market)
        return life,gate,ex,bot,provider,trade,report,account,start

    def test_spot_full_account_cash_fees_remaining_exit_cost_and_native_release(self):
        life,gate,_,_,provider,trade,report,e,_=self.filled()
        checked,audit=self.checkpoint(provider,e)
        self.assertEqual(audit["fees_paid"],"0.05")
        self.assertEqual(D(audit["exit_cost_reserve"]),D(".065"))
        self.assertEqual(D(audit["observed_equity"]),D("999.885"))
        self.assertEqual(life.risk.equity(checked.snapshot),D("999.885"))
        result=gate.reconcile_native("r1","source-account",report,checked,report.received_at,
            fills=provider.campaign_fills("campaign-"+str(trade.id)),fills_complete=True,sequence_basis="source_ordered")
        self.assertEqual(result["state"],"TERMINAL_RECONCILED")
        self.assertEqual(checked.snapshot.day_start_equity,D(1000))

    def test_futures_unpaid_then_settled_funding_has_same_nav_and_native_sign(self):
        life,_,_,_,provider,trade,report,e,_=self.filled("futures")
        event=self.event(trade,2,seconds=2,kind="funding_accrual",funding_id="btc-fund1",cost_quote="2")
        provider.append(event);trade.set_funding_fees(-2);Trade.commit()
        t=T+timedelta(seconds=2)
        report=replace(report,received_at=t)
        e=self.account(report,trade,"5","999.95",cursor=2,market="futures",funding=(Funding("btc-fund1",D(2),False),))
        before,audit=self.checkpoint(provider,e)
        self.assertEqual(D(audit["observed_equity"]),D("997.91"))
        provider.append(self.event(trade,3,seconds=3,kind="funding_settlement",funding_id="btc-fund1",cost_quote="2"))
        report=replace(report,received_at=T+timedelta(seconds=3))
        e=self.account(report,trade,"5","997.95",cursor=3,market="futures",funding=(Funding("btc-fund1",D(2),True),))
        after,audit=self.checkpoint(provider,e)
        self.assertEqual(life.risk.equity(before.snapshot),life.risk.equity(after.snapshot))
        self.assertEqual(audit["settled_funding_cash"],"-2")
        self.assertEqual(audit["pending_funding_cost"],"0")

    def test_losing_open_position_and_partial_exit_remain_in_nav(self):
        life,_,_,_,provider,trade,report,e,_=self.filled()
        at=T+timedelta(seconds=2)
        raw=dict(id="exit1",symbol=trade.pair,side="sell",type="limit",status="closed",amount=2.,filled=2.,remaining=0.,
                 price=9.,average=9.,timestamp=int(at.timestamp()*1000))
        order=Order.parse_from_ccxt_object(raw,trade.pair,"sell");trade.orders.append(order);trade.update_trade(order);Trade.commit()
        provider.append(self.event(trade,2,quantity="-2",fee=".018",seconds=2,order_id="exit1",price="9"))
        e=self.account(replace(report,received_at=at),trade,"3","967.932",cursor=3)
        e=replace(e,snapshot=replace(e.snapshot,quotes=tuple(replace(q,price=D(8)) if q.pair==trade.pair else q for q in e.snapshot.quotes)))
        checked,audit=self.checkpoint(provider,e)
        self.assertEqual(D(audit["realized_price_pnl"]),D(-2))
        self.assertEqual(D(audit["fees_paid"]),D(".068"))
        self.assertEqual(D(audit["observed_equity"]),D("991.9008"))
        self.assertEqual(life.risk.equity(checked.snapshot),D("991.9008"))
        later=at+timedelta(seconds=1)
        falling=replace(e,snapshot=replace(e.snapshot,at=later,balance_at=later,orders_at=later,cursor=4,
            quotes=tuple(replace(q,at=later,price=D(1) if q.pair==trade.pair else q.price) for q in e.snapshot.quotes)))
        checked,audit=self.checkpoint(provider,falling)
        self.assertEqual(D(audit["observed_equity"]),D("970.9281"))
        decision=life.risk.evaluate(checked.snapshot,later)
        self.assertEqual(decision.state,"EXIT_PENDING")
        self.assertIn("daily_loss",decision.reasons)
        self.assertTrue(any(a["kind"]=="EXIT_POSITION" for a in decision.actions))

    def test_restart_reconstructs_exact_ledger_and_checkpoint_without_double_booking(self):
        life,_,_,_,provider,trade,_,e,_=self.filled()
        first=self.checkpoint(provider,e)
        original=copy.deepcopy(provider.s["events"][0]);provider.close();provider=self.provider(life,create=False)
        self.assertFalse(provider.append(original))
        self.assertEqual(provider.ledger.wallet,D("949.95"))
        self.assertEqual(self.checkpoint(provider,e),first)

    def test_missing_wallet_charge_missing_funding_and_unknown_coverage_reject(self):
        _,_,_,_,provider,trade,report,e,_=self.filled("futures")
        with self.assertRaisesRegex(RiskError,"wallet differs"):
            self.checkpoint(provider,replace(e,snapshot=replace(e.snapshot,balance=D(1000))))
        provider.append(self.event(trade,2,seconds=2,kind="funding_accrual",funding_id="fund1",cost_quote="2"))
        report=replace(report,received_at=T+timedelta(seconds=2));e=self.account(report,trade,"5","999.95",market="futures")
        with self.assertRaisesRegex(RiskError,"funding differs"): self.checkpoint(provider,e)
        with self.assertRaisesRegex(RiskError,"coverage must be explicit"):
            provider.checkpoint(e,self.receipts(e),e.snapshot.at,complete_sequence=2,complete_from_inception=True,funding_complete=False)

    def test_native_funding_sign_error_is_not_hidden_by_correct_wallet(self):
        _,_,_,_,provider,trade,report,e,_=self.filled("futures")
        provider.append(self.event(trade,2,seconds=2,kind="funding_settlement",funding_id="fund1",cost_quote="2"))
        trade.set_funding_fees(2);Trade.commit()
        report=replace(report,received_at=T+timedelta(seconds=2))
        e=self.account(report,trade,"5","997.95",market="futures",funding=(Funding("fund1",D(2),True),))
        with self.assertRaisesRegex(RiskError,"Native campaign funding"): self.checkpoint(provider,e)

    def test_missing_midnight_cannot_be_replaced_with_first_later_sample(self):
        _,_,_,_,provider,_,report,e,_=self.filled()
        next_day=MIDNIGHT+timedelta(days=1,seconds=1)
        snap=ef.snapshot(at=next_day,cursor=3,balance="949.95",positions=e.snapshot.positions)
        e=replace(e,snapshot=snap)
        with self.assertRaisesRegex(RiskError,"Exact UTC midnight"): self.checkpoint(provider,e)

    def test_future_quote_receipt_and_false_mark_kind_are_rejected(self):
        _,_,_,_,provider,_,_,e,_=self.filled()
        receipts=self.receipts(e)
        with self.assertRaisesRegex(RiskError,"availability invalid"):
            provider.checkpoint(e,(replace(receipts[0],received_at=e.snapshot.at+timedelta(seconds=1)),)+receipts[1:],e.snapshot.at,
                complete_sequence=1,complete_from_inception=True,funding_complete=True)
        wrong=replace(e,snapshot=replace(e.snapshot,quotes=tuple(replace(q,kind="bar_close") for q in e.snapshot.quotes)))
        with self.assertRaisesRegex(RiskError,"valuation quote"): self.checkpoint(provider,wrong)

    def test_missing_native_fill_event_and_wrong_basis_cannot_certify_inventory(self):
        _,_,_,_,provider,trade,_,e,_=self.filled()
        trade.orders[0].average=11.;Trade.commit()
        wrong=replace(e,snapshot=replace(e.snapshot,positions=tuple(replace(p,entry_price=D(11)) for p in e.snapshot.positions)))
        with self.assertRaisesRegex(RiskError,"cost basis differs"): self.checkpoint(provider,wrong)
        trade.orders[0].average=10.;trade.orders[0].filled=4.;trade.orders[0].remaining=1.;Trade.commit()
        with self.assertRaisesRegex(RiskError,"cumulative fills"): self.checkpoint(provider,e)

    def test_event_gap_conflicting_duplicate_and_reused_fill_do_not_change_state(self):
        _,_,_,_,provider,trade,_,e,_=self.filled()
        before=copy.deepcopy(provider.s)
        for event in (self.event(trade,3),self.event(trade,1,fee=".1"),self.event(trade,2,fill_id="f1")):
            with self.assertRaises(RiskError): provider.append(event)
            self.assertEqual(provider.s,before)

    def test_event_after_complete_checkpoint_cannot_backdate_its_receipt(self):
        _,_,_,_,provider,trade,_,e,_=self.filled()
        self.checkpoint(provider,e)
        with self.assertRaisesRegex(RiskError,"Late event contradicts"):
            provider.append(self.event(trade,2,quantity="1"))
        with self.assertRaisesRegex(RiskError,"Late event contradicts"):
            provider.append(self.event(trade,2,quantity="1",received_at=stamp(T+timedelta(seconds=3))))

    def test_commit_ack_loss_reopens_from_durable_events_without_second_fee(self):
        life,gate,ex,bot,e,p,provider,_=self.harness()
        self.execute(ex,bot,e,p);trade,_=self.order_event(gate)
        event=self.event(trade);save=provider.store.save
        def save_then_fail(state):
            save(state);raise OSError("synthetic commit acknowledgement lost")
        with patch.object(provider.store,"save",side_effect=save_then_fail):
            with self.assertRaisesRegex(StateError,"not acknowledged"): provider.append(event)
        with self.assertRaisesRegex(StateError,"unavailable"): provider.append(event)
        provider.close();provider=self.provider(life,create=False)
        self.assertFalse(provider.append(event))
        self.assertEqual(provider.ledger.wallet,D("949.95"))
        self.assertEqual(provider.ledger.fees,D(".05"))

    def test_guarded_native_fill_provider_and_lifecycle_use_the_same_fee_ledger(self):
        life,gate,ex,bot,e,p,provider,_=self.harness()
        guard=AfterFillGuard(self.root/"protection",life.risk,create=True);self.opened.append(guard)
        gate.after_fill_guard=guard
        self.execute(ex,bot,e,p);gate.bind_native("r1");trade=Trade.get_trades_proxy()[0]
        offline=OfflineProtectionExchange("spot",[ef.market_fixture("spot")],{},"synthetic-explicit-rules");self.opened.append(offline)
        bot.exchange=offline;bot.strategy=resolved("F1Momentum168Native",(p.pair,));bot.config["dry_run"]=True;bot.margin_mode=offline.margin_mode
        bot.wallets.get_free=Mock(return_value=0);bot.order_close_notify=Mock()
        for name in ("order_obj_or_raise","handle_order_fee","get_real_amount","apply_fee_conditional","cancel_stoploss_on_exchange"):
            setattr(bot,name,MethodType(getattr(FreqtradeBot,name),bot))
        raw=copy.deepcopy(gate.s["requests"]["r1"]["ccxt_payload"])
        raw.update(status="closed",filled=5.,remaining=0.,average=10.,cost=50.,fee={"currency":"USDT","cost":.05,"rate":.001})
        report=CapturedOrder(json.dumps(raw),T+timedelta(seconds=1),"synthetic-native-fill","synthetic")
        guard.apply("actual-native-fill",bot,trade,report,report.received_at)
        provider.append(self.event(trade))
        checked,audit=self.checkpoint(provider,self.account(report,trade,"5","949.95"))
        result=gate.reconcile_native("r1","provider-native",report,checked,report.received_at,
            fills=provider.campaign_fills("campaign-"+str(trade.id)),fills_complete=True,sequence_basis="source_ordered")
        guard.require_entry_clear()
        self.assertEqual(result["state"],"TERMINAL_RECONCILED")
        self.assertEqual(trade.stop_loss,9.5)
        self.assertEqual(D(str(trade.fee_open_cost)),D(audit["fees_paid"]))

    def test_open_partial_counts_actual_inventory_and_pending_remainder_separately(self):
        life,gate,ex,bot,e,p,provider,_=self.harness()
        self.execute(ex,bot,e,p)
        trade,report=self.order_event(gate,status="open",filled=2)
        provider.append(self.event(trade,quantity="2",fee=".02"))
        checked,audit=self.checkpoint(provider,self.account(report,trade,"2","979.98"))
        self.assertEqual(trade.amount,0)
        self.assertEqual(checked.snapshot.positions[0].quantity,D(2))
        self.assertEqual(checked.snapshot.orders[0].remaining_quantity,D(3))
        self.assertEqual(D(audit["observed_equity"]),D("999.954"))
        result=gate.reconcile_native("r1","provider-partial",report,checked,report.received_at,
            fills=provider.campaign_fills("campaign-"+str(trade.id)),fills_complete=True,sequence_basis="source_ordered")
        self.assertEqual(result["state"],"OBSERVED_OPEN")

    def test_next_midnight_anchor_requires_fresh_complete_positions_and_quotes(self):
        _,_,_,_,provider,trade,_,e,_=self.filled()
        self.checkpoint(provider,e)
        midnight=MIDNIGHT+timedelta(days=1)
        e=replace(e,snapshot=ef.snapshot(at=midnight,cursor=3,balance="949.95",positions=e.snapshot.positions))
        checked,audit=self.checkpoint(provider,e)
        self.assertEqual(checked.snapshot.day_start_equity,D("999.885"))
        later=midnight+timedelta(seconds=1)
        e=replace(e,snapshot=ef.snapshot(at=later,cursor=4,balance="949.95",positions=e.snapshot.positions,
            day_start_equity=D(1234)))
        checked,_=self.checkpoint(provider,e)
        self.assertEqual(checked.snapshot.day_start_equity,D("999.885"))
        self.assertEqual(provider.s["checkpoints"]["4"]["input"]["evidence"]["snapshot"]["day_start_equity"],"1234")

    def test_missing_watermark_and_nanosecond_truncation_are_not_certified(self):
        _,_,_,_,provider,trade,_,e,_=self.filled()
        with self.assertRaisesRegex(RiskError,"watermark differs"): self.checkpoint(provider,e,sequence=0)
        with self.assertRaisesRegex(RiskError,"microsecond precision"):
            provider.append(self.event(trade,2,event_at="2026-11-06T12:00:02.000000001Z",received_at="2026-11-06T12:00:02.000000001Z"))
        self.assertEqual(provider.ledger.last_sequence,1)

    def test_checkpoint_ack_loss_returns_no_permission_and_restarts_from_committed_anchor(self):
        life,_,_,_,_,_,provider,e=self.harness(initialize=False)
        save=provider.store.save
        def save_then_fail(state):
            save(state);raise OSError("synthetic checkpoint acknowledgement lost")
        with patch.object(provider.store,"save",side_effect=save_then_fail):
            with self.assertRaisesRegex(StateError,"not acknowledged"): self.checkpoint(provider,e)
        provider.close();provider=self.provider(life,create=False)
        checked,audit=self.checkpoint(provider,e)
        self.assertEqual(checked.snapshot.day_start_equity,D(1000))
        self.assertEqual(provider.s["last_cursor"],0)

    def test_closed_native_historical_amount_is_zero_current_exposure(self):
        life,_,_,_,provider,trade,report,e,_=self.filled()
        at=T+timedelta(seconds=2)
        raw=dict(id="exit1",symbol=trade.pair,side="sell",type="limit",status="closed",amount=5.,filled=5.,remaining=0.,
                 price=9.,average=9.,timestamp=int(at.timestamp()*1000))
        order=Order.parse_from_ccxt_object(raw,trade.pair,"sell");trade.orders.append(order);trade.update_trade(order);Trade.commit()
        self.assertFalse(trade.is_open)
        self.assertEqual(trade.amount,5)
        provider.append(self.event(trade,2,quantity="-5",fee=".045",seconds=2,order_id="exit1",price="9"))
        e=self.account(replace(report,received_at=at),balance="994.905",cursor=3)
        checked,audit=self.checkpoint(provider,e)
        self.assertEqual(checked.snapshot.positions,())
        self.assertEqual(audit["exit_cost_reserve"],"0")
        self.assertEqual(life.risk.equity(checked.snapshot),D("994.905"))
        self.assertEqual(provider.ledger.fees,D(".095"))


if __name__=="__main__":
    unittest.main()
