"""Isolated SQLite/native Trade checks; synthetic targets/orders, no exchange I/O."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "sources"))
from native_reductions import KEY, ReductionController, ReductionTarget, scale_target
from freqtrade.enums import TradingMode
from freqtrade.freqtradebot import FreqtradeBot
from freqtrade.persistence import CustomDataWrapper, Order, Trade, init_db
from freqtrade.persistence.custom_data import _CustomData
from test_native_factors import resolved


class Reductions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="factor-reduction-test-")
        self.root = Path(self.temp.name)
        self.dburl = "sqlite:///" + str(self.root / "isolated.sqlite")
        init_db(self.dburl)
        CustomDataWrapper.use_db = True
        self.now = datetime.now(timezone.utc)
        self.trade = Trade(pair="BTC/USDT", stake_currency="USDT", exchange="binance",
                           is_open=True, amount=10, stake_amount=100, open_rate=10,
                           open_date=self.now, fee_open=.0013, fee_close=.0013,
                           leverage=1, is_short=False, trading_mode=TradingMode.SPOT)
        Trade.session.add(self.trade)
        self.trade.orders.append(self.order("entry", 10, 10, "closed", "buy", 10))
        self.trade.recalc_trade_from_orders()
        Trade.commit()
        self.controller = ReductionController(self.root, "synthetic-account")

    def tearDown(self):
        self.controller.close()
        engine = Trade.session.get_bind()
        Trade.session.remove()
        _CustomData.session.remove()
        engine.dispose()
        self.temp.cleanup()

    def order(self, tag, amount, filled, status="closed", side="sell", price=20):
        raw = {"id": tag, "symbol": "BTC/USDT", "type": "limit", "side": side,
               "price": price, "average": price, "amount": amount, "filled": filled,
               "remaining": None if filled is None else amount-filled,
               "status": status, "timestamp": int(self.now.timestamp()*1000)}
        order = Order.parse_from_ccxt_object(raw, "BTC/USDT", side, amount, price)
        order.ft_order_tag = tag
        return order

    def target(self, cap="5", sequence=1):
        return ReductionTarget("synthetic-account", self.trade.id, self.trade.pair,
                               sequence, self.now, str(self.trade.amount if self.trade.is_open else 0), cap, "synthetic_RD1")

    def fill(self, tag, amount, filled, status="closed"):
        order = self.order(tag, amount, filled, status)
        self.trade.orders.append(order)
        self.trade.recalc_trade_from_orders()
        Trade.commit()
        return order

    def test_claim_persists_in_real_database_and_second_process_observes_unknown(self):
        amount, tag = self.controller.issue(self.trade, self.target(), self.now)
        self.assertEqual(amount, -50)
        code = "from freqtrade.persistence import init_db,Trade; import sys,json; init_db(sys.argv[1]); t=Trade.session.get(Trade,int(sys.argv[2])); print(json.dumps(t.get_custom_data(sys.argv[3])))"
        state = json.loads(subprocess.check_output([sys.executable, "-B", "-c", code, self.dburl, str(self.trade.id), KEY], text=True))
        self.assertEqual(state["attempts"][-1]["tag"], tag)
        self.assertEqual(state["attempts"][-1]["status"], "ISSUED_UNKNOWN")
        self.controller.close()
        self.controller = ReductionController(self.root, "synthetic-account")
        self.assertIsNone(self.controller.issue(self.trade, self.target(sequence=2), self.now))

    def test_partial_cancellation_reconciles_then_only_remaining_quantity_exits(self):
        _, tag = self.controller.issue(self.trade, self.target(), self.now)
        self.fill(tag, 5, 2, "canceled")
        self.assertAlmostEqual(self.trade.amount, 8)
        amount, next_tag = self.controller.issue(self.trade, self.target(sequence=2), self.now)
        self.assertAlmostEqual(amount, -30)
        self.assertNotEqual(tag, next_tag)
        self.fill(next_tag, 3, 3)
        self.assertAlmostEqual(self.trade.amount, 5)
        self.assertIsNone(self.controller.issue(self.trade, self.target(sequence=3), self.now))
        self.assertIsNone(self.controller.issue(self.trade, self.target(sequence=4), self.now))
        self.assertEqual(len(self.trade.get_custom_data(KEY)["attempts"]), 2)

    def test_open_partial_order_blocks_duplicate_and_missing_fill_is_unknown(self):
        _, tag = self.controller.issue(self.trade, self.target(), self.now)
        order = self.fill(tag, 5, 2, "open")
        self.assertIsNone(self.controller.issue(self.trade, self.target(sequence=2), self.now))
        order.status = "canceled"; order.ft_is_open = False; order.filled = None
        Trade.commit()
        with self.assertRaisesRegex(ValueError, "missing its filled"):
            self.controller.issue(self.trade, self.target(sequence=2), self.now)

    def test_closed_native_trade_historical_amount_is_not_remaining_exposure(self):
        _, tag = self.controller.issue(self.trade, self.target(cap="0"), self.now)
        self.trade.orders.append(self.order(tag, 10, 10))
        self.trade.close(20, show_msg=False)
        Trade.commit()
        self.assertFalse(self.trade.is_open)
        self.assertEqual(self.trade.amount, 10)
        self.assertIsNone(self.controller.issue(self.trade, self.target(cap="0", sequence=2), self.now))
        self.assertEqual(self.trade.get_custom_data(KEY)["attempts"][-1]["status"], "RECONCILED")

    def test_no_increase_or_dca_and_forced_exit_survives_missing_factor(self):
        self.assertIsNone(self.controller.issue(self.trade, self.target(cap="20"), self.now))
        target = self.target(cap="0", sequence=2)
        self.assertEqual(scale_target(target, float("nan")), target)
        amount, _ = self.controller.issue(self.trade, target, self.now)
        self.assertEqual(amount, -100)

    def test_identity_staleness_conflict_and_dirty_native_transaction_rejected(self):
        for bad in (replace(self.target(), account_id="other"),
                    replace(self.target(), observed_at=self.now-timedelta(seconds=16)),
                    replace(self.target(), observed_quantity="9")):
            with self.assertRaises(ValueError): self.controller.issue(self.trade, bad, self.now)
        self.trade.stake_amount = 99
        with self.assertRaisesRegex(RuntimeError, "uncommitted"):
            self.controller.issue(self.trade, self.target(), self.now)
        Trade.session.rollback()
        self.controller.issue(self.trade, self.target(), self.now)
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.controller.issue(self.trade, self.target(cap="4"), self.now)

    def test_database_commit_then_failure_returns_no_action_and_restart_does_not_resubmit(self):
        commit = _CustomData.session.commit
        def fail_after_commit():
            commit()
            raise OSError("injected loss of acknowledgement after durable commit")
        with patch.object(_CustomData.session, "commit", side_effect=fail_after_commit):
            with self.assertRaises(OSError):
                self.controller.issue(self.trade, self.target(), self.now)
        self.assertTrue(self.controller.poisoned)
        self.controller.close()
        self.controller = ReductionController(self.root, "synthetic-account")
        self.assertIsNone(self.controller.issue(self.trade, self.target(sequence=2), self.now))

    def test_account_lock_excludes_another_writer(self):
        with self.assertRaises(BlockingIOError):
            ReductionController(self.root, "synthetic-account")

    def test_separate_native_metadata_transaction_cannot_be_committed_implicitly(self):
        self.assertIsNot(Trade.session, _CustomData.session)
        row = _CustomData(ft_trade_id=self.trade.id, cd_key="unrelated", cd_type="str", cd_value="pending")
        _CustomData.session.add(row)
        with self.assertRaisesRegex(RuntimeError, "uncommitted"):
            self.controller.issue(self.trade, self.target(), self.now)
        _CustomData.session.rollback()
        self.assertIsNone(self.trade.get_custom_data(KEY))
        with patch.object(Trade.session, "get_bind", return_value=object()):
            with self.assertRaisesRegex(RuntimeError, "binding changed"):
                self.controller.issue(self.trade, self.target(), self.now)

    def test_native_position_dispatch_uses_cost_stake_and_never_submits_entry(self):
        strategy = resolved("F2Volatility24Native")
        strategy.rd1_reduction_controller = self.controller
        strategy.rd1_reduction_target = lambda trade, now: replace(self.target(cap="10"), observed_at=now)
        strategy.factor_scale = lambda pair, now: .5
        calls = []
        bot = object.__new__(FreqtradeBot)
        bot.strategy = strategy
        bot.exchange = SimpleNamespace(get_rates=lambda *a: (20, 20),
            get_min_pair_stake_amount=lambda *a: 5,
            get_max_pair_stake_amount=lambda *a: 1000,
            amount_to_contract_precision=lambda pair, amount: amount)
        bot.wallets = SimpleNamespace(get_available_stake_amount=lambda: 1000)
        bot.execute_entry = lambda *a, **kw: self.fail("Positive adjustment submitted")
        bot.execute_trade_exit = lambda *a, **kw: calls.append(kw)
        bot.check_and_call_adjust_trade_position(self.trade)
        self.assertEqual(calls[0]["sub_trade_amt"], 5)
        bot.check_and_call_adjust_trade_position(self.trade)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
