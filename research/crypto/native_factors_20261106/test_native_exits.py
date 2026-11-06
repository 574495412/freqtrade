"""Actual native forced exits; only local synthetic transport and temporary DB."""
import copy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import ccxt
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/"sources"))
from native_exits import LocalExitExchange, NativeRiskExit
from native_entries import LocalBinanceTape
from native_evidence import CapturedOrder
from rd1 import RiskError,StateError,digest,Funding
from freqtrade.persistence import Trade
from freqtrade.exceptions import TemporaryError
import test_native_entries as ef
import test_native_wallets as wf


class NativeExitTests(unittest.TestCase):
    setUp=ef.NativeEntryTests.setUp
    tearDown=ef.NativeEntryTests.tearDown
    provider=wf.NativeWalletTests.provider
    checkpoint=wf.NativeWalletTests.checkpoint
    receipts=wf.NativeWalletTests.receipts
    event=wf.NativeWalletTests.event
    account=wf.NativeWalletTests.account
    execute=wf.NativeWalletTests.execute
    receipt=wf.NativeWalletTests.receipt
    boot=wf.NativeWalletTests.boot
    fill_cycle=wf.NativeWalletTests.fill_cycle

    def harness(self,market="spot"):
        result=wf.NativeWalletTests.harness(self,market)
        self.entry_gate=result[1]
        return result

    def exit_harness(self,market="spot",status="NEW",lose_reply=False):
        wallet,provider,guard,trade,bot,e,_,_,now=self.fill_cycle(market)
        now+=timedelta(seconds=1)
        e=replace(e,snapshot=replace(e.snapshot,at=now,balance_at=now,orders_at=now,cursor=3,
            quotes=tuple(replace(q,at=now,price=D(5) if q.pair==trade.pair else q.price) for q in e.snapshot.quotes)))
        with wallet.clock.at(now):
            wallet.stage("drawdown",self.receipt(e,quantity="5",cash_used="50" if market=="futures" else "0",collateral="50",upnl="-25"),e,now)
            checked,_=self.checkpoint(provider,e);wallet.confirm(checked,now)
            decision=provider.risk.evaluate(checked.snapshot,now)
        action=next(a for a in decision.actions if a["kind"]=="EXIT_POSITION")
        controller=NativeRiskExit(self.root/"exits",self.entry_gate,wallet,create=True);self.opened.append(controller)
        client="rd1x-"+digest({"account":provider.risk.s["identity"],"action":action["id"]})[:28]
        raw={"symbol":"BTCUSDT","orderId":200,"clientOrderId":client,"time":int(now.timestamp()*1000),
            "transactTime":int(now.timestamp()*1000),"updateTime":int(now.timestamp()*1000),"price":"5.00","origQty":"5.00",
            "executedQty":"5.00" if status=="FILLED" else "0.00","cummulativeQuoteQty":"25.00" if status=="FILLED" else "0.00",
            "cumQuote":"25.00" if status=="FILLED" else "0.00","avgPrice":"5.00" if status=="FILLED" else "0.00",
            "status":status,"timeInForce":"GTC","type":"LIMIT","side":"SELL","reduceOnly":True}
        tape=LocalBinanceTape([ef.market_fixture(market)],[ccxt.RequestTimeout("synthetic lost exit reply") if lose_reply else (raw,now)],allowed_methods=("POST","GET"))
        ex=LocalExitExchange(tape,controller);self.opened.append(ex)
        return controller,ex,wallet,provider,guard,trade,bot,checked,now,raw,action

    def test_native_spot_exit_bypasses_strategy_veto_and_records_real_order(self):
        c,ex,wallet,p,guard,trade,bot,e,now,raw,action=self.exit_harness()
        with wallet.clock.at(now),patch.object(bot.strategy,"confirm_trade_exit",return_value=False) as veto:
            aid=c.dispatch(trade,e,ex,bot,now,limit="5")
        veto.assert_not_called()
        self.assertEqual(aid,action["id"])
        self.assertEqual(len(ex._api.requests),1)
        self.assertEqual(ex._api.requests[0]["params"]["side"],"SELL")
        self.assertEqual(ex._api.requests[0]["params"]["quantity"],"5")
        self.assertEqual(trade.orders[-1].ft_order_tag,c.s["actions"][aid]["client_order_id"])
        self.assertTrue(trade.is_open)
        self.assertTrue(trade.orders[-1].ft_is_open)
        self.assertEqual(c.s["actions"][aid]["state"],"NATIVE_BOUND_ACCOUNT_PENDING")

    def full_exit(self,market,funding_cost=0):
        c,ex,wallet,p,guard,trade,bot,e,now,raw,action=self.exit_harness(market)
        funding=()
        if funding_cost:
            now+=timedelta(seconds=1)
            p.append(self.event(trade,2,seconds=3,kind="funding_settlement",funding_id="settled-before-exit",cost_quote=str(funding_cost)))
            trade.set_funding_fees(-funding_cost);Trade.commit()
            funding=(Funding("settled-before-exit",D(funding_cost),True),)
            e=replace(e,snapshot=replace(e.snapshot,at=now,balance_at=now,orders_at=now,cursor=4,balance=D("999.95")-funding_cost,
                funding=funding,quotes=tuple(replace(q,at=now) for q in e.snapshot.quotes)))
            with wallet.clock.at(now):
                wallet.stage("funded-drawdown",self.receipt(e,quantity="5",cash_used="50",collateral="50",upnl="-25"),e,now)
                e,_=self.checkpoint(p,e);wallet.confirm(e,now)
            raw.update(time=int(now.timestamp()*1000),transactTime=int(now.timestamp()*1000),updateTime=int(now.timestamp()*1000))
            ex._api.responses=[(raw,now)]
        with wallet.clock.at(now):aid=c.dispatch(trade,e,ex,bot,now,limit="5")
        now+=timedelta(seconds=1)
        terminal=copy.deepcopy(raw);terminal.update(status="FILLED",executedQty="5",cummulativeQuoteQty="25",cumQuote="25",avgPrice="5",updateTime=int(now.timestamp()*1000))
        ex._api.responses=[(terminal,now)]
        with wallet.clock.at(now):ex.query(aid,now)
        p.append(self.event(trade,p.ledger.last_sequence+1,quantity="-5",fee=".025",seconds=int((now-ef.T).total_seconds()),order_id="200",price="5"))
        report=c.envelope(aid,now)
        account=self.account(report,balance=str(D("974.925")-funding_cost),cursor=p.s["last_cursor"]+1,market=market,funding=funding)
        with wallet.clock.at(now):
            bot.wallets=wallet.stage("exit-filled",self.receipt(account),account,now)
            c.apply_filled(aid,bot,now)
            checked,audit=self.checkpoint(p,account);wallet.confirm(checked,now)
            result=c.reconcile(aid,checked,now)
        self.assertFalse(trade.is_open)
        self.assertEqual(checked.snapshot.positions,())
        self.assertEqual(D(audit["observed_equity"]),D("974.925")-funding_cost)
        self.assertEqual(D(audit["fees_paid"]),D(".075"))
        self.assertEqual(result["state"],"TERMINAL_RECONCILED")
        self.assertFalse(result["requires_further_exit"])
        self.assertEqual(p.risk.s["pending_actions"][aid]["status"],"satisfied")
        self.assertIn("daily_loss",p.risk.s["latches"])
        self.assertEqual(len(ex._api.requests),2)
        if funding_cost:self.assertEqual(trade.funding_fees,-funding_cost)
        return c,ex

    def test_spot_exit_full_fee_wallet_and_rd1_action_reconciliation(self):
        self.full_exit("spot")

    def test_futures_exit_is_reduce_only_and_reconciles_wallet_once(self):
        _,ex=self.full_exit("futures")
        self.assertTrue(ex._api.requests[0]["params"]["reduceOnly"])
        self.assertEqual(ex._api.requests[0]["api"],["fapiPrivate"])

    def test_native_funding_refresh_does_not_reset_or_double_count_settled_funding(self):
        self.full_exit("futures",funding_cost=2)

    def test_lost_exit_reply_restart_does_not_repeat_submission(self):
        c,ex,wallet,p,guard,trade,bot,e,now,raw,action=self.exit_harness(lose_reply=True)
        with wallet.clock.at(now):
            with self.assertRaises(TemporaryError):c.dispatch(trade,e,ex,bot,now,limit="5")
        Trade.session.rollback()
        c.close();c=NativeRiskExit(self.root/"exits",self.entry_gate,wallet);self.opened.append(c);ex.controller=c
        with wallet.clock.at(now):
            with self.assertRaisesRegex(StateError,"already attempted"):c.dispatch(trade,e,ex,bot,now,limit="5")
        self.assertEqual(len(ex._api.requests),1)
        self.assertEqual(len(trade.orders),1)

    def test_immediate_fill_is_deferred_until_explicit_wallet_and_fee_evidence(self):
        c,ex,wallet,p,guard,trade,bot,e,now,raw,action=self.exit_harness(status="FILLED")
        with wallet.clock.at(now):aid=c.dispatch(trade,e,ex,bot,now,limit="5")
        self.assertTrue(trade.is_open)
        self.assertEqual(trade.amount,5)
        self.assertEqual(trade.orders[-1].filled,5)
        self.assertTrue(c.s["actions"][aid]["terminal_callback_deferred"])
        with wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError,"not proven"):c.apply_filled(aid,bot,now)
            with self.assertRaises(RiskError):wallet.require_entry_clear(now)

    def test_wrong_exit_response_and_futures_missing_reduce_only_never_bind(self):
        c,ex,wallet,p,guard,trade,bot,e,now,raw,action=self.exit_harness("futures")
        raw["reduceOnly"]=False
        with wallet.clock.at(now):
            with self.assertRaisesRegex(RiskError,"reduceOnly differs"):c.dispatch(trade,e,ex,bot,now,limit="5")
        Trade.session.rollback()
        self.assertEqual(len(trade.orders),1)
        self.assertIsNone(c.s["actions"][action["id"]]["native_binding"])

    def test_exit_intent_persist_failure_never_reaches_transport(self):
        c,ex,wallet,p,guard,trade,bot,e,now,raw,action=self.exit_harness()
        with wallet.clock.at(now),patch.object(c.store,"save",side_effect=OSError("synthetic disk failure")):
            with self.assertRaisesRegex(StateError,"not acknowledged"):c.dispatch(trade,e,ex,bot,now,limit="5")
        self.assertEqual(ex._api.requests,[])

    def test_native_commit_ack_loss_can_bind_existing_order_without_new_request(self):
        c,ex,wallet,p,guard,trade,bot,e,now,raw,action=self.exit_harness()
        commit=Trade.commit
        def commit_then_fail():commit();raise RuntimeError("synthetic native acknowledgement lost")
        with wallet.clock.at(now),patch.object(Trade,"commit",side_effect=commit_then_fail):
            with self.assertRaisesRegex(RuntimeError,"acknowledgement lost"):c.dispatch(trade,e,ex,bot,now,limit="5")
        Trade.session.remove()
        with wallet.clock.at(now):c.bind(action["id"],now)
        self.assertEqual(len(ex._api.requests),1)

    def test_open_order_prevents_overlapping_risk_exit(self):
        c,ex,wallet,p,guard,trade,bot,e,now,raw,action=self.exit_harness()
        with wallet.clock.at(now):
            c.dispatch(trade,e,ex,bot,now,limit="5")
            with self.assertRaises(RiskError):c.dispatch(trade,e,ex,bot,now,limit="5")
        self.assertEqual(len(ex._api.requests),1)

    def test_partial_cancel_retries_only_remaining_inventory_after_new_reconciliation(self):
        c,ex,wallet,p,guard,trade,bot,e,now,raw,action=self.exit_harness()
        with wallet.clock.at(now):aid=c.dispatch(trade,e,ex,bot,now,limit="5")
        now+=timedelta(seconds=1)
        terminal=copy.deepcopy(raw);terminal.update(status="CANCELED",executedQty="2",cummulativeQuoteQty="10",cumQuote="10",avgPrice="5")
        ex._api.responses=[(terminal,now)]
        with wallet.clock.at(now):ex.query(aid,now)
        p.append(self.event(trade,2,quantity="-2",fee=".01",seconds=3,order_id="200",price="5"))
        e=self.account(c.envelope(aid,now),trade,"3","959.94",cursor=4)
        e=replace(e,snapshot=replace(e.snapshot,quotes=tuple(replace(q,price=D(5)) if q.pair==trade.pair else q for q in e.snapshot.quotes)))
        with wallet.clock.at(now):
            bot.wallets=wallet.stage("partial-exit",self.receipt(e,quantity="3"),e,now)
            c.apply_filled(aid,bot,now)
            checked,_=self.checkpoint(p,e);wallet.confirm(checked,now)
            result=c.reconcile(aid,checked,now)
            self.assertTrue(result["requires_further_exit"])
            self.assertEqual(trade.amount,3)
            with self.assertRaisesRegex(StateError,"already attempted"):c.dispatch(trade,checked,ex,bot,now,limit="5")
        now+=timedelta(seconds=1)
        e=replace(e,snapshot=replace(e.snapshot,at=now,balance_at=now,orders_at=now,cursor=5,quotes=tuple(replace(q,at=now) for q in e.snapshot.quotes)))
        client="rd1x-"+digest({"account":p.risk.s["identity"],"action":aid,"attempt":2})[:28]
        second=copy.deepcopy(raw);second.update(orderId=201,clientOrderId=client,origQty="3",time=int(now.timestamp()*1000),transactTime=int(now.timestamp()*1000),updateTime=int(now.timestamp()*1000))
        ex._api.responses=[(second,now)]
        prior_checked=checked
        with wallet.clock.at(now):
            bot.wallets=wallet.stage("retry-ready",self.receipt(e,quantity="3"),e,now)
            checked,_=self.checkpoint(p,e);wallet.confirm(checked,now)
            with self.assertRaisesRegex(RiskError,"checked account evidence"):
                c.reconcile(aid,prior_checked,now)
            c.dispatch(trade,checked,ex,bot,now,limit="5")
        self.assertEqual(ex._api.requests[-1]["params"]["quantity"],"3")
        self.assertEqual(c.s["actions"][aid]["attempt"],2)
        self.assertEqual(len(c.s["actions"][aid]["prior_attempts"]),1)
        now+=timedelta(seconds=1);second.update(status="FILLED",executedQty="3",cummulativeQuoteQty="15",cumQuote="15",avgPrice="5")
        ex._api.responses=[(second,now)]
        with wallet.clock.at(now):ex.query(aid,now)
        p.append(self.event(trade,3,quantity="-3",fee=".015",seconds=5,order_id="201",price="5"))
        e=self.account(c.envelope(aid,now),balance="974.925",cursor=6)
        with wallet.clock.at(now):
            bot.wallets=wallet.stage("flat",self.receipt(e),e,now)
            c.apply_filled(aid,bot,now);checked,audit=self.checkpoint(p,e);wallet.confirm(checked,now)
            result=c.reconcile(aid,checked,now)
        self.assertFalse(result["requires_further_exit"])
        self.assertFalse(trade.is_open)
        self.assertEqual(D(audit["observed_equity"]),D("974.925"))


if __name__=="__main__":unittest.main()
