"""Real native Wallets and scoped event time, no private exchange transport."""
import copy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import importlib
import json
from pathlib import Path
import sys
from types import MethodType
import unittest
from unittest.mock import Mock, patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/"sources"))
from native_wallets import NativeWalletBridge, WalletReceipt
from native_entries import NativeEntryGate
from native_clock import NativeReplayClock
from native_after_fill import AfterFillGuard, OfflineProtectionExchange
from native_evidence import CapturedOrder
from rd1 import RiskError, StateError
from freqtrade.freqtradebot import FreqtradeBot
from freqtrade.persistence import Trade, PairLocks
from freqtrade.wallets import Wallets, Wallet
import test_native_entries as ef
import test_native_account as af
from test_native_factors import resolved

T=ef.T; MIDNIGHT=af.MIDNIGHT


class NativeWalletTests(unittest.TestCase):
    setUp=ef.NativeEntryTests.setUp
    tearDown=ef.NativeEntryTests.tearDown
    provider=af.NativeAccountTests.provider
    checkpoint=af.NativeAccountTests.checkpoint
    receipts=af.NativeAccountTests.receipts
    event=af.NativeAccountTests.event
    account=af.NativeAccountTests.account
    execute=ef.NativeEntryTests.execute

    def receipt(self,e,*,cash_used="0",quantity="0",collateral="0",upnl="0"):
        cash=e.snapshot.balance;used=D(cash_used)
        balances={"USDT":{"free":str(cash-used),"used":str(used),"total":str(cash)}}
        positions=()
        if e.market=="spot" and D(quantity):
            balances["BTC"]={"free":quantity,"used":"0","total":quantity}
        elif D(quantity):
            positions=({"symbol":ef.pair(e.market),"side":"long","contracts":quantity,"leverage":"1",
                        "initialMargin":collateral,"unrealizedPnl":upnl,"entryPrice":"10"},)
        return WalletReceipt(e.account_id,e.market,"synthetic-wallet-receipt","synthetic",e.snapshot.at,
            e.snapshot.balance_at,e.snapshot.orders_at,e.snapshot.balance_semantics,balances,positions,True,True)

    def harness(self,market="spot"):
        life,gate,ex,bot,e,p,provider,start=af.NativeAccountTests.harness(self,market)
        guard=AfterFillGuard(self.root/"protection",life.risk,create=True);self.opened.append(guard);gate.after_fill_guard=guard
        wallet=NativeWalletBridge(self.root/"wallet",provider,[ef.market_fixture(market)],create=True);self.opened.append(wallet)
        wallet.attach(gate)
        return life,gate,ex,bot,e,p,provider,start,guard,wallet

    def boot(self,provider,wallet,e,event="boot"):
        checked,_=self.checkpoint(provider,e)
        native=wallet.stage(event,self.receipt(e),e,e.snapshot.at)
        wallet.confirm(checked,e.snapshot.at)
        wallet.require_entry_clear(e.snapshot.at)
        return native

    def test_bootstrap_requires_account_match_and_uses_actual_native_wallet(self):
        _,gate,ex,bot,e,p,provider,start,_,wallet=self.harness()
        with wallet.clock.at(MIDNIGHT):
            with self.assertRaisesRegex(RiskError,"pending"):wallet.require_entry_clear(MIDNIGHT)
            native=self.boot(provider,wallet,start)
            self.assertIs(type(native),Wallets)
            self.assertEqual(native.get_total("USDT"),1000)
            self.assertEqual(native.get_free("USDT"),1000)
            self.assertEqual(native._last_wallet_refresh,MIDNIGHT)
        with self.assertRaisesRegex(StateError,"clock is not active"):wallet.require_entry_clear(MIDNIGHT)
        with wallet.clock.at(T):
            with self.assertRaisesRegex(RiskError,"stale"):self.execute(ex,bot,e,p)
        self.assertEqual(ex._api.requests,[])

    def fill_cycle(self,market):
        life,gate,ex,bot,e,p,provider,start,guard,wallet=self.harness(market)
        e=replace(e,source_id=af.SOURCE,snapshot=replace(e.snapshot,cursor=1))
        with wallet.clock.at(T):
            bot.wallets=self.boot(provider,wallet,e,"pre-entry")
            self.execute(ex,bot,e,p)
            trade=Trade.get_trades_proxy()[0];gate.bind_native("r1")
            self.assertEqual(trade.open_date_utc,T)
            with self.assertRaisesRegex(RiskError,"open order inventories differ"):wallet.require_entry_clear(T)
        tiers={p.pair:[{"minNotional":0,"maxNotional":50000,"maintenanceMarginRate":.004,"maintAmt":0}]} if market=="futures" else {}
        offline=OfflineProtectionExchange(market,[ef.market_fixture(market)],tiers,"synthetic-rule-table");self.opened.append(offline)
        bot.exchange=offline;bot.strategy=resolved("F1Momentum168Native",(p.pair,));bot.config["dry_run"]=True;bot.margin_mode=offline.margin_mode
        bot.order_close_notify=Mock()
        for name in ("order_obj_or_raise","handle_order_fee","get_real_amount","apply_fee_conditional","cancel_stoploss_on_exchange"):
            setattr(bot,name,MethodType(getattr(FreqtradeBot,name),bot))
        raw=copy.deepcopy(gate.s["requests"]["r1"]["ccxt_payload"])
        raw.update(status="closed",filled=5.,remaining=0.,average=10.,cost=50.,fee={"currency":"USDT","cost":.05,"rate":.001})
        now=T+timedelta(seconds=1)
        report=CapturedOrder(json.dumps(raw),now,"synthetic-fill-event","synthetic")
        e=self.account(report,trade,"5","949.95" if market=="spot" else "999.95",market=market)
        if market=="futures":
            e=replace(e,snapshot=replace(e.snapshot,positions=tuple(replace(pos,collateral=D(50)) for pos in e.snapshot.positions)))
        provider.append(self.event(trade))
        receipt=self.receipt(e,quantity="5",cash_used="0" if market=="spot" else "50",collateral="50")
        times=[]
        with wallet.clock.at(now):
            bot.wallets=wallet.stage("filled",receipt,e,now)
            with self.assertRaisesRegex(RiskError,"pending"):wallet.require_entry_clear(now)
            with patch.object(bot.strategy,"order_filled",side_effect=lambda **kw:times.append(kw["current_time"])):
                guard.apply("fill",bot,trade,report,now)
            checked,audit=self.checkpoint(provider,e)
            wallet.confirm(checked,now)
            result=gate.reconcile_native("r1","wallet-account",report,checked,now,fills=provider.campaign_fills("campaign-"+str(trade.id)),
                fills_complete=True,sequence_basis="source_ordered")
            wallet.require_entry_clear(now);guard.require_entry_clear()
        self.assertEqual(times,[now])
        self.assertEqual(trade.orders[0].order_update_date.replace(tzinfo=T.tzinfo),now)
        self.assertEqual(bot.wallets._last_wallet_refresh,now)
        self.assertEqual(result["state"],"TERMINAL_RECONCILED")
        self.assertEqual(trade.stop_loss,9.5)
        self.assertEqual(bot.wallets.get_owned(trade.pair,"BTC"),5)
        self.assertEqual(bot.wallets.get_total("USDT"),float(e.snapshot.balance))
        self.assertEqual(audit["fees_paid"],"0.05")
        return wallet,provider,guard,trade,bot,e,checked,receipt,now

    def test_spot_entry_fill_wallet_account_and_protection_share_event_time(self):
        self.fill_cycle("spot")

    def test_futures_native_wallet_uses_wallet_cash_and_separate_collateral(self):
        wallet,_,_,trade,bot,_,_,_,_=self.fill_cycle("futures")
        self.assertEqual(bot.wallets.get_total("USDT"),999.95)
        self.assertEqual(bot.wallets.get_free("USDT"),949.95)
        self.assertEqual(bot.wallets.get_all_positions()[trade.pair].collateral,50)
        self.assertEqual(trade.liquidation_price,.5)

    def test_clock_restores_all_bindings_on_exception_and_rejects_nesting(self):
        clock=NativeReplayClock()
        original={(m,n):getattr(importlib.import_module(m),n) for m,names in clock.TARGETS.items() for n in names}
        with self.assertRaisesRegex(RuntimeError,"synthetic failure"):
            with clock.at(T):
                for (module,name),_ in original.items():
                    obj=getattr(importlib.import_module(module),name)
                    self.assertEqual(obj.now(T.tzinfo) if name=="datetime" else obj(),T)
                with self.assertRaisesRegex(StateError,"Nested"):
                    with clock.at(T):pass
                with patch.object(PairLocks,"timeframe","5m"):
                    lock=PairLocks.lock_pair("BTC/USDT",T+timedelta(minutes=5),reason="synthetic replay")
                self.assertEqual(lock.lock_time.replace(tzinfo=T.tzinfo),T)
                raise RuntimeError("synthetic failure")
        for (m,n),old in original.items():self.assertIs(getattr(importlib.import_module(m),n),old)
        with patch("native_clock.threading.active_count",return_value=2):
            with self.assertRaisesRegex(StateError,"single-threaded"):
                with clock.at(T):pass

    def test_unverified_wallet_cannot_submit_even_when_guard_has_no_pending_fills(self):
        _,gate,ex,bot,e,p,provider,_,guard,wallet=self.harness()
        e=replace(e,source_id=af.SOURCE)
        with wallet.clock.at(T):
            bot.wallets=wallet.stage("unchecked",self.receipt(e),e,T)
            with self.assertRaisesRegex(RiskError,"pending"):self.execute(ex,bot,e,p)
        self.assertEqual(ex._api.requests,[])
        self.assertEqual(gate.s["requests"],{})

    def test_missing_wallet_fields_and_hidden_cash_cannot_be_defaulted_to_zero(self):
        _,_,_,_,_,_,provider,e,_,wallet=self.harness()
        receipt=self.receipt(e)
        with wallet.clock.at(MIDNIGHT):
            missing=copy.deepcopy(receipt.balances);del missing["USDT"]["used"]
            with self.assertRaisesRegex(RiskError,"default zero"):wallet.stage("missing",replace(receipt,balances=missing),e,MIDNIGHT)
            extra=copy.deepcopy(receipt.balances);extra["BTC"]={"free":"1","used":"0","total":"1"}
            with self.assertRaisesRegex(RiskError,"base wallet differs"):wallet.stage("extra",replace(receipt,balances=extra),e,MIDNIGHT)
        self.assertEqual(wallet.s["frames"],{})

    def test_native_wallet_mutation_and_source_mutation_block_entry(self):
        _,_,_,_,_,_,provider,e,_,wallet=self.harness()
        with wallet.clock.at(MIDNIGHT):
            self.boot(provider,wallet,e)
            wallet.wallets._wallets["USDT"]=Wallet("USDT",1200,0,1200)
            with self.assertRaisesRegex(RiskError,"amounts differ"):wallet.require_entry_clear(MIDNIGHT)
            wallet.source.frame["balances"]["USDT"].update(free=1200,total=1200)
            with self.assertRaisesRegex(RiskError,"payload changed"):wallet.require_entry_clear(MIDNIGHT)

    def test_restart_needs_explicit_wallet_and_account_bootstrap(self):
        _,_,_,_,_,_,provider,e,_,wallet=self.harness()
        with wallet.clock.at(MIDNIGHT):self.boot(provider,wallet,e)
        wallet.close();wallet=NativeWalletBridge(self.root/"wallet",provider,[ef.market_fixture("spot")]);self.opened.append(wallet)
        with wallet.clock.at(MIDNIGHT):
            with self.assertRaisesRegex(RiskError,"reconstructed"):wallet.require_entry_clear(MIDNIGHT)
            self.boot(provider,wallet,e)

    def test_wallet_commit_ack_loss_never_returns_entry_permission(self):
        _,_,_,_,_,_,provider,e,_,wallet=self.harness()
        with wallet.clock.at(MIDNIGHT):
            checked,_=self.checkpoint(provider,e)
            wallet.stage("boot",self.receipt(e),e,MIDNIGHT)
            save=wallet.store.save
            def save_then_fail(state):save(state);raise OSError("synthetic acknowledgement lost")
            with patch.object(wallet.store,"save",side_effect=save_then_fail):
                with self.assertRaisesRegex(StateError,"not acknowledged"):wallet.confirm(checked,MIDNIGHT)
            with self.assertRaisesRegex(StateError,"unavailable"):wallet.require_entry_clear(MIDNIGHT)

    def test_failed_fresh_wallet_observation_revokes_old_confirmation(self):
        _,_,_,_,_,_,provider,e,_,wallet=self.harness()
        with wallet.clock.at(MIDNIGHT):
            self.boot(provider,wallet,e)
            checked,_=self.checkpoint(provider,e)
            receipt=self.receipt(e);missing=copy.deepcopy(receipt.balances);del missing["USDT"]["free"]
            with self.assertRaisesRegex(RiskError,"default zero"):wallet.stage("invalid",replace(receipt,balances=missing),e,MIDNIGHT)
            with self.assertRaisesRegex(RiskError,"pending"):wallet.require_entry_clear(MIDNIGHT)
            with self.assertRaisesRegex(RiskError,"failed validation"):wallet.confirm(checked,MIDNIGHT)

    def test_negative_unrealized_pnl_does_not_get_subtracted_from_wallet_twice(self):
        wallet,provider,guard,trade,bot,e,_,_,now=self.fill_cycle("futures")
        now+=timedelta(seconds=1)
        e=replace(e,snapshot=replace(e.snapshot,at=now,balance_at=now,orders_at=now,cursor=3,
            quotes=tuple(replace(q,at=now,price=D(9) if q.pair==trade.pair else q.price) for q in e.snapshot.quotes)))
        receipt=self.receipt(e,quantity="5",cash_used="50",collateral="50",upnl="-5")
        with wallet.clock.at(now):
            bad=copy.deepcopy(receipt.balances);bad["USDT"].update(free="944.95",total="994.95")
            with self.assertRaisesRegex(RiskError,"observed account cash"):wallet.stage("equity-as-wallet",replace(receipt,balances=bad),e,now)
            bot.wallets=wallet.stage("mark-down",receipt,e,now)
            checked,audit=self.checkpoint(provider,e);wallet.confirm(checked,now);wallet.require_entry_clear(now)
        self.assertEqual(bot.wallets.get_total("USDT"),999.95)
        self.assertEqual(bot.wallets.get_all_positions()[trade.pair].unrealized_pnl,-5)
        self.assertEqual(D(audit["observed_equity"]),D("994.914"))

    def test_open_entry_reservation_cannot_be_reported_as_free_wallet_cash(self):
        _,gate,ex,bot,e,p,provider,_,_,wallet=self.harness()
        e=replace(e,source_id=af.SOURCE)
        with wallet.clock.at(T):
            bot.wallets=self.boot(provider,wallet,e)
            self.execute(ex,bot,e,p)
        report=CapturedOrder(json.dumps(gate.s["requests"]["r1"]["ccxt_payload"]),T+timedelta(seconds=1),"synthetic-fresh-open-order","synthetic")
        e=self.account(report)
        with wallet.clock.at(report.received_at):
            with self.assertRaisesRegex(RiskError,"omits entry reservations"):
                wallet.stage("bad-free",self.receipt(e),e,report.received_at)
            wallet.stage("reserved",self.receipt(e,cash_used="50"),e,report.received_at)
            checked,_=self.checkpoint(provider,e);wallet.confirm(checked,report.received_at);wallet.require_entry_clear(report.received_at)
            self.assertEqual(wallet.wallets.get_total("USDT"),1000)
            self.assertEqual(wallet.wallets.get_free("USDT"),950)

    def test_unreconciled_new_event_blocks_otherwise_unchanged_wallet(self):
        wallet,provider,guard,trade,_,e,_,_,now=self.fill_cycle("futures")
        later=now+timedelta(seconds=1)
        provider.append(self.event(trade,2,seconds=2,kind="funding_accrual",funding_id="f1",cost_quote="1"))
        with wallet.clock.at(later):
            with self.assertRaisesRegex(RiskError,"Native campaign funding|watermark changed"):
                wallet.require_entry_clear(later)

    def test_reopened_entry_gate_cannot_forget_required_wallet_bootstrap(self):
        life,gate,ex,bot,e,p,provider,_,guard,wallet=self.harness()
        e=replace(e,source_id=af.SOURCE)
        with wallet.clock.at(T):
            self.boot(provider,wallet,e)
        gate.close()
        reopened=NativeEntryGate(self.root/"spot-receipts",life,after_fill_guard=guard);self.opened.append(reopened)
        ex.gate=reopened
        with wallet.clock.at(T):
            with self.assertRaisesRegex(StateError,"must be reattached"):self.execute(ex,bot,e,p)
            self.assertEqual(ex._api.requests,[])
            wallet.attach(reopened)
            self.assertTrue(self.execute(ex,bot,e,p))
            self.assertEqual(len(ex._api.requests),1)


if __name__=="__main__":unittest.main()
