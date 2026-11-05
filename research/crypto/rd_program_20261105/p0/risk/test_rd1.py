"""Synthetic unit/fault evidence only; never opens deployment state or market data."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rd1 import (RD1, Correlations, Entry, Funding, Order, Position, Quote,
                 RiskError, Snapshot, StateError, canonical, digest, load_contract)

UTC = timezone.utc
T = datetime(2026, 11, 6, 12, tzinfo=UTC)


def pair(market, asset="BTC"):
    return f"{asset}/USDT" + (":USDT" if market == "futures" else "")


def snapshot(market="spot", at=T, cursor=1, balance="1000", positions=(), orders=(), **kwargs):
    default_pairs = [pair(market, x) for x in ("BTC", "ETH", "SOL", "AVAX", "DOGE", "XRP", "ADA")]
    quotes = tuple(Quote(p, D(10), at, "spot_bid" if market == "spot" else "futures_mark") for p in default_pairs)
    values = dict(cursor=cursor, at=at, balance_at=at, orders_at=at, balance=D(balance),
                  balance_semantics=("total_cash_including_reserved" if market == "spot" else
                                     "wallet_excluding_upnl_including_collateral_and_settled_flows"),
                  positions=tuple(positions), orders=tuple(orders), quotes=quotes,
                  funding=(), funding_complete=True, day_start_equity=D(1000),
                  day_start_at=at.replace(hour=0, minute=0, second=0, microsecond=0),
                  reconciliation_passed=True, reconciliation_difference=D(0))
    values.update(kwargs)
    return Snapshot(**values)


def entry(market="spot", **kwargs):
    values = dict(pair=pair(market), quantity=D(5), price=D(10), leverage=D(1),
                  stop_distance=D(".05"), spread_bps=D(10), previous_hour_base_volume=D(100000),
                  visible_contra_depth_within_20bps=D(10000), liquidity_at=T,
                  amount_step=D(".01"), minimum_notional=D(5))
    values.update(kwargs)
    return Entry(**values)


def position(market="spot", asset="BTC", quantity="5", entry_price="10", cost="0", id=None):
    return Position(id or asset + "-lifecycle-1", pair(market, asset), D(quantity), D(entry_price), D(cost))


def order(market="spot", asset="BTC", quantity="5", id="entry-1", **kwargs):
    return Order(id, pair(market, asset), D(quantity), D(10), T, True, **kwargs)


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engines = []

    def tearDown(self):
        for engine in self.engines:
            engine.close()
        self.tmp.cleanup()

    def engine(self, market="spot", name="account", create=True, **kwargs):
        engine = RD1(Path(self.tmp.name) / name, name, market, create=create, **kwargs)
        self.engines.append(engine)
        return engine

    def renew(self, engine, snap, seconds=5, **kwargs):
        at = snap.at + timedelta(seconds=seconds)
        values = dict(cursor=snap.cursor + 1, at=at, balance_at=at, orders_at=at,
                      quotes=tuple(replace(q, at=at) for q in snap.quotes))
        values.update(kwargs)
        return replace(snap, **values)

    def test_frozen_contract_loaded_and_runtime_forbidden(self):
        plan, digest_value = load_contract()
        self.assertEqual(plan["risk_contract"]["id"], "RD1")
        self.assertFalse(plan["runtime_changes_allowed"])
        self.assertEqual(len(digest_value), 64)

    def test_spot_reserved_cash_not_subtracted_twice(self):
        e = self.engine()
        s = snapshot(balance="900", positions=[position(quantity="10", cost="1")], orders=[order(quantity="2")])
        self.assertEqual(e.equity(s), D(999))
        self.assertEqual(e.evaluate(s, T).exposure["gross"], "120")

    def test_futures_collateral_and_settled_funding_not_double_counted(self):
        e = self.engine("futures")
        p = replace(position("futures", quantity="10", entry_price="9", cost="2"), collateral=D(500))
        s = snapshot("futures", balance="990", positions=[p],
                     funding=(Funding("paid", D(10), True), Funding("unpaid", D(3), False)))
        self.assertEqual(e.equity(s), D(995))  # 990+10-3-2, never +500 or -10 again
        settled = replace(s, balance=D(987), funding=(Funding("paid", D(10), True), Funding("unpaid", D(3), True)))
        self.assertEqual(e.equity(settled), D(995))

    def test_funding_credit_sign_and_duplicate_rejection(self):
        e = self.engine("futures")
        s = snapshot("futures", funding=(Funding("credit", D(-2), False),))
        self.assertEqual(e.equity(s), D(1002))
        bad = replace(s, funding=s.funding * 2)
        self.assertFalse(e.evaluate(bad, T).entry_allowed)
        self.assertIn("technical", e.s["latches"])

    def test_wrong_balance_semantics_and_missing_funding_fail_closed(self):
        for market, kwargs in [("spot", {"balance_semantics": "free_cash"}),
                               ("futures", {"funding_complete": False})]:
            with self.subTest(market=market):
                e = self.engine(market, name=market)
                result = e.evaluate(snapshot(market, **kwargs), T)
                self.assertIsNone(result.equity)
                self.assertFalse(result.entry_allowed)

    def test_absolute_floor_equality_float_loss_and_exit_cost(self):
        for market in ("spot", "futures"):
            with self.subTest(market=market):
                e = self.engine(market, name=market)
                p = position(market, quantity="100", entry_price="10", cost="1")
                balance = "0" if market == "spot" else "1000"
                s = snapshot(market, balance=balance, positions=[p])
                s = replace(s, quotes=tuple(replace(q, price=D("5.01")) if q.pair == p.pair else q for q in s.quotes))
                result = e.evaluate(s, T)
                self.assertEqual(result.equity, "500.00")
                self.assertIn("absolute_floor", result.reasons)
                self.assertEqual(result.state, "EXIT_PENDING")
                self.assertEqual(len([a for a in result.actions if a["kind"] == "EXIT_POSITION"]), 1)

    def test_accounts_are_independent_not_netting_surplus(self):
        spot, futures = self.engine(name="spot"), self.engine("futures", name="futures")
        self.assertTrue(futures.evaluate(snapshot("futures", balance="1600"), T).entry_allowed)
        result = spot.evaluate(snapshot(balance="500"), T)
        self.assertIn("absolute_floor", result.reasons)
        self.assertEqual(result.state, "HALTED")

    def test_hwm_equality_15_percent_and_restart_survives(self):
        e = self.engine()
        e.evaluate(snapshot(balance="1100"), T)
        s = snapshot(at=T + timedelta(seconds=5), cursor=2, balance="935", day_start_equity=D(935))
        # keep day baseline immutable: initial above used 1000, so use same baseline
        s = replace(s, day_start_equity=D(1000))
        result = e.evaluate(s, s.at)
        self.assertIn("hwm_hard_halt", result.reasons)
        e.close()
        resumed = self.engine(create=False)
        self.assertEqual(resumed.s["high_water_mark"], "1100")
        self.assertIn("hwm_hard_halt", resumed.s["latches"])
        next_s = self.renew(resumed, s, balance=D(1100))
        self.assertFalse(resumed.evaluate(next_s, next_s.at).entry_allowed)

    def test_warning_10_percent_is_sticky_and_requires_review(self):
        e = self.engine()
        s = snapshot(balance="900", day_start_equity=D(900))
        result = e.evaluate(s, T)
        self.assertEqual(result.state, "DE_RISK")
        self.assertEqual(result.actions[0]["payload"]["gross_fraction"], "0.3")
        s = self.renew(e, s, balance=D(1000))
        self.assertFalse(e.evaluate(s, s.at).entry_allowed)
        with self.assertRaises(StateError):
            e.review_recovery(s.at, authorization_id="", incident_review="review", clear_warning=True)
        e.review_recovery(s.at, authorization_id="synthetic-review-1", incident_review="synthetic test only", clear_warning=True)
        self.assertFalse(e.evaluate(s, s.at).entry_allowed)  # old cursor cannot release post-review barrier
        s = self.renew(e, s)
        self.assertTrue(e.evaluate(s, s.at).entry_allowed)

    def test_day_loss_2_percent_equality_next_day_requires_flat_no_orders(self):
        e = self.engine()
        s = snapshot(balance="980")
        self.assertIn("daily_loss", e.evaluate(s, T).reasons)
        at = T + timedelta(days=1)
        p = position(quantity="1")
        s = snapshot(at=at, cursor=2, balance="970", positions=[p], day_start_equity=D(980))
        self.assertIn("daily_loss", e.evaluate(s, at).reasons)
        s = self.renew(e, s, balance=D(980), positions=(), orders=(order(),))
        self.assertIn("daily_loss", e.evaluate(s, s.at).reasons)
        s = self.renew(e, s, orders=())
        self.assertNotIn("daily_loss", e.evaluate(s, s.at).reasons)

    def test_midnight_or_same_day_baseline_cannot_be_reinvented(self):
        e = self.engine()
        s = snapshot()
        e.evaluate(s, T)
        changed = self.renew(e, s, balance=D(970), day_start_equity=D(970))
        self.assertFalse(e.evaluate(changed, changed.at).entry_allowed)
        e2 = self.engine(name="late-baseline")
        late = snapshot(day_start_at=T)
        self.assertIn("unknown_utc_day_baseline", e2.evaluate(late, T).reasons)

    def test_hard_latch_never_cleared_by_day_rollover_or_fresh_data(self):
        e = self.engine()
        e.evaluate(snapshot(balance="500"), T)
        next_day = snapshot(at=T + timedelta(days=1), cursor=2, balance="500", day_start_equity=D(500))
        result = e.evaluate(next_day, next_day.at)
        self.assertFalse(result.entry_allowed)
        self.assertIn("absolute_floor", result.reasons)
        with self.assertRaises(StateError):
            e.review_recovery(next_day.at, authorization_id="review", incident_review="test", clear_hard=True)

    def test_explicit_hard_review_needs_fresh_flat_reconciliation_and_no_reset(self):
        e = self.engine()
        s = snapshot(balance="500")
        e.evaluate(s, T)
        s = self.renew(e, s, balance=D(1000))  # synthetic recovery, not a deposit
        e.evaluate(s, s.at)
        e.review_recovery(s.at, authorization_id="explicit-test", incident_review="cause-reviewed", clear_hard=True, clear_warning=True)
        self.assertEqual(e.s["initial_equity"], "1000")
        self.assertEqual(e.s["high_water_mark"], "1000")
        self.assertNotIn("absolute_floor", e.s["latches"])
        self.assertIn("daily_loss", e.s["latches"])  # day lock remains separately governed

    def test_stale_clock_api_and_reconcile_exact_boundaries(self):
        cases = [("stale_data", {"balance_at": T - timedelta(seconds=16)}),
                 ("clock_skew", {"exchange_clock_skew_seconds": D("2.01")}),
                 ("api_errors", {"consecutive_api_errors": 3}),
                 ("equity_reconciliation_mismatch", {"reconciliation_difference": D(".0101")}),
                 ("unreconciled_orders_positions", {"unknown_orders": True})]
        for i, (reason, kw) in enumerate(cases):
            with self.subTest(reason=reason):
                e = self.engine(name=f"bad{i}")
                self.assertIn(reason, e.evaluate(snapshot(**kw), T).reasons)
        e = self.engine(name="exact")
        good = snapshot(balance_at=T-timedelta(seconds=15), exchange_clock_skew_seconds=D(2),
                        consecutive_api_errors=2, reconciliation_difference=D(".01"))
        self.assertTrue(e.evaluate(good, T).entry_allowed)

    def test_technical_recovery_needs_five_continuous_minutes(self):
        e = self.engine()
        s = snapshot(transport_available=False)
        e.evaluate(s, T)
        s = self.renew(e, s, transport_available=True)
        self.assertFalse(e.evaluate(s, s.at).entry_allowed)
        for _ in range(59):
            s = self.renew(e, s)
            self.assertFalse(e.evaluate(s, s.at).entry_allowed)
        s = self.renew(e, s)
        self.assertTrue(e.evaluate(s, s.at).entry_allowed)

    def test_recovery_gap_or_repeated_heartbeat_does_not_count(self):
        e = self.engine()
        s = snapshot(transport_available=False)
        e.evaluate(s, T)
        s = self.renew(e, s, transport_available=True)
        e.evaluate(s, s.at)
        for _ in range(30):
            s = self.renew(e, s)
            e.evaluate(s, s.at)
        s = self.renew(e, s, seconds=16)
        e.evaluate(s, s.at)
        start = e.s["fresh_since"]
        for _ in range(5):
            self.assertFalse(e.evaluate(s, s.at).entry_allowed)
        self.assertEqual(e.s["fresh_since"], start)
        self.assertFalse(e.evaluate(s, s.at + timedelta(seconds=300)).entry_allowed)

    def test_missing_and_nonfinite_inputs_never_become_zero(self):
        cases = [snapshot(balance=D("NaN")), snapshot(balance=D("Infinity")),
                 replace(snapshot(), balance=1000.0), snapshot(quotes=(), positions=(position(),))]
        for i, s in enumerate(cases):
            with self.subTest(i=i):
                result = self.engine(name=f"bad{i}").evaluate(s, T)
                self.assertFalse(result.entry_allowed)
                self.assertIsNone(result.equity)

    def test_deposit_cannot_reset_loss_or_peak(self):
        e = self.engine()
        result = e.evaluate(snapshot(balance="1500", external_capital_flow=D(500)), T)
        self.assertFalse(result.entry_allowed)
        self.assertEqual(e.s["high_water_mark"], "1000")

    def test_duplicate_and_conflicting_cursors(self):
        e = self.engine()
        s = snapshot(balance="500", positions=(position(),))
        one = e.evaluate(s, T)
        two = e.evaluate(s, T)
        self.assertEqual([a["id"] for a in one.actions], [a["id"] for a in two.actions])
        bad = replace(s, balance=D(1000))
        self.assertIn("technical", e.evaluate(bad, T).reasons)
        self.assertIn("technical", e.evaluate(replace(s, cursor=0), T).reasons)

    def test_partial_fill_cancel_and_exit_are_idempotent(self):
        e = self.engine()
        protected = replace(order(id="protect", quantity="-2"), increases_risk=False, reduce_only=True)
        s = snapshot(balance="400", positions=(position(quantity="2"),), orders=(order(quantity="3"), protected))
        first = e.evaluate(s, T)
        cancel = next(a for a in first.actions if a["kind"] == "CANCEL_ENTRY")
        exit_a = next(a for a in first.actions if a["kind"] == "EXIT_POSITION")
        self.assertEqual(cancel["target"], "entry-1")
        self.assertFalse(any(a["target"] == "protect" for a in first.actions))
        e.claim_action(exit_a["id"], T)
        with self.assertRaises(StateError):
            e.claim_action(exit_a["id"], T)
        # Entry partially fills before cancellation: move qty from remainder to position ONCE.
        s = self.renew(e, s, positions=(position(quantity="3"),), orders=(order(quantity="2"), protected), balance=D(390))
        result = e.evaluate(s, s.at)
        exit_b = next(a for a in result.actions if a["kind"] == "EXIT_POSITION")
        self.assertEqual(exit_a["id"], exit_b["id"])
        self.assertEqual(exit_b["payload"]["remaining_quantity"], "3")
        self.assertEqual(result.exposure["gross"], "50")
        e.claim_action(exit_b["id"], s.at)
        s = self.renew(e, s, positions=(position(quantity="1"),), orders=(), balance=D(410))
        result = e.evaluate(s, s.at)
        self.assertEqual(next(a for a in result.actions if a["kind"] == "EXIT_POSITION")["payload"]["remaining_quantity"], "1")
        s = self.renew(e, s, positions=(), balance=D(420))
        result = e.evaluate(s, s.at)
        self.assertEqual(result.state, "HALTED")
        self.assertEqual(result.actions, ())

    def test_network_outage_preserves_intents_but_forbids_blind_retry(self):
        e = self.engine()
        s = snapshot(balance="400", positions=(position(),), orders=(order(),), transport_available=False)
        result = e.evaluate(s, T)
        self.assertTrue(result.actions)
        self.assertTrue(all(not a["claimable"] for a in result.actions))
        with self.assertRaises(StateError):
            e.claim_action(result.actions[0]["id"], T)
        s = self.renew(e, s, transport_available=True)
        result = e.evaluate(s, s.at)
        # Fresh reconciliation permits risk reduction, even while hard/technical locks forbid entries.
        self.assertTrue(all(a["claimable"] for a in result.actions))
        self.assertFalse(result.entry_allowed)

    def test_entry_timeout_cancels_only_unfilled_remainder(self):
        e = self.engine()
        protected = replace(order(id="protect", quantity="-1"), increases_risk=False, reduce_only=True)
        s = snapshot(orders=(order(quantity="2"), protected))
        s = self.renew(e, s, seconds=60)
        result = e.evaluate(s, s.at)
        cancels = [a for a in result.actions if a["kind"] == "CANCEL_ENTRY"]
        self.assertEqual(len(cancels), 1)
        self.assertEqual(cancels[0]["payload"]["remaining_quantity"], "2")

    def test_new_trade_and_positive_adjustment_same_gate(self):
        e = self.engine()
        self.assertTrue(e.assess_entry(snapshot(), entry(), T)[0])
        rejected = entry(positive_adjustment=True)
        self.assertIn("dca_disabled", e.assess_entry(snapshot(), rejected, T)[1])
        s = snapshot(cursor=2, balance="950", positions=(position(),))
        self.assertIn("dca_disabled", e.assess_entry(s, entry(), T)[1])

    def test_trade_risk_includes_two_sided_cost_and_stress_is_tighter(self):
        base = self.engine(name="base")
        stress = self.engine(name="stress", cost_scenario="stress")
        proposed = entry(quantity=D("9.4"))  # 94*.0526 <5, 94*.054 >5
        self.assertTrue(base.assess_entry(snapshot(), proposed, T)[0])
        self.assertIn("trade_risk_including_roundtrip_cost", stress.assess_entry(snapshot(), proposed, T)[1])
        self.assertIn("trade_risk_including_roundtrip_cost", base.assess_entry(snapshot(), entry(quantity=D(10)), T)[1])

    def test_pending_orders_count_in_pair_exposure(self):
        e = self.engine()
        s = snapshot(orders=(order(quantity="6"),))
        result = e.assess_entry(s, entry(quantity=D(5)), T)
        self.assertFalse(result[0])
        self.assertIn("pair_exposure", result[1])

    def test_missing_correlation_one_cluster_and_transitive_complete_matrix(self):
        e = self.engine()
        positions = tuple(position(asset=a, quantity="10") for a in ("BTC", "ETH", "SOL"))
        s = snapshot(balance="700", positions=positions)
        self.assertIn("cluster_exposure", e.assess_entry(s, entry(pair=pair("spot", "AVAX")), T)[1])
        edges = []
        for a, b in itertools.combinations(sorted(e.universe), 2):
            correlated = {a, b} in [{pair("spot", "BTC"), pair("spot", "ADA")},
                                    {pair("spot", "ETH"), pair("spot", "ADA")}]
            edges.append((a, b, D(".7") if correlated else D(0)))
        s = replace(s, correlations=Correlations(T, 720, tuple(edges)), cursor=2)
        groups, complete = e.clusters(s)
        self.assertTrue(complete)
        self.assertEqual(groups[pair("spot", "BTC")], groups[pair("spot", "ETH")])
        # The connection goes through ADA even though ADA is not held.
        self.assertNotEqual(groups[pair("spot", "BTC")], groups[pair("spot", "SOL")])
        self.assertTrue(e.assess_entry(s, entry(pair=pair("spot", "AVAX")), T)[0])
        bad = replace(s, correlations=Correlations(T, 719, tuple(edges)))
        self.assertFalse(e.clusters(bad)[1])

    def test_total_exposure_count_and_leverage_controls(self):
        e = self.engine()
        ps = tuple(position(asset=a, quantity="10") for a in ("BTC", "ETH", "SOL", "AVAX", "DOGE", "XRP", "ADA"))
        s = snapshot(balance="300", positions=ps)
        result = e.evaluate(s, T)
        self.assertIn("gross_exposure", result.reasons)
        self.assertIn("net_exposure", result.reasons)
        self.assertIn("position_count", result.reasons)
        self.assertEqual(result.state, "DE_RISK")
        e2 = self.engine("futures", name="leveraged")
        result = e2.assess_entry(snapshot("futures"), entry("futures", leverage=D(2)), T)
        self.assertIn("leverage", result[1])

    def test_liquidity_precision_and_min_notional_negative_cases(self):
        fields = [("spread", {"spread_bps": D("20.01")}),
                  ("volume_participation", {"previous_hour_base_volume": D(4000)}),
                  ("visible_depth", {"visible_contra_depth_within_20bps": D(400)}),
                  ("stale_liquidity", {"liquidity_at": T-timedelta(seconds=16)}),
                  ("amount_precision", {"quantity": D("5.001")}),
                  ("minimum_notional_skip", {"minimum_notional": D(100)}),
                  ("unregistered_stop_distance", {"stop_distance": D(".04")})]
        for i, (reason, values) in enumerate(fields):
            with self.subTest(reason=reason):
                e = self.engine(name=f"liq{i}")
                self.assertIn(reason, e.assess_entry(snapshot(), entry(**values), T)[1])

    def test_new_snapshot_price_kind_and_short_pnl(self):
        e = self.engine("futures")
        p = position("futures", quantity="-10", entry_price="11")
        s = snapshot("futures", positions=(p,))
        self.assertEqual(e.equity(s), D(1010))
        wrong = replace(s, quotes=tuple(replace(q, kind="last_trade") for q in s.quotes))
        self.assertFalse(e.evaluate(wrong, T).entry_allowed)

    def test_concurrent_writer_and_identity_mismatch_fail_closed(self):
        e = self.engine()
        with self.assertRaises(StateError):
            self.engine(create=False)
        e.close()
        with self.assertRaises(StateError):
            self.engine("futures", create=False)
        with self.assertRaises(StateError):
            self.engine(create=True)

    def test_corrupt_or_missing_state_does_not_initialize(self):
        e = self.engine()
        path = e.store.directory / "state.json"
        e.close()
        path.write_text('{"state":"damaged"}')
        with self.assertRaises(StateError):
            self.engine(create=False)
        path.unlink()
        with self.assertRaises(StateError):
            self.engine(create=False)
        with self.assertRaises(StateError):
            self.engine(create=True)

    def test_checksum_and_schema_faults_fail_closed(self):
        e = self.engine()
        path = e.store.directory / "state.json"
        e.close()
        envelope = json.loads(path.read_text())
        envelope["state"]["initial_equity"] = "2000"
        path.write_bytes(canonical(envelope))
        with self.assertRaises(StateError):
            self.engine(create=False)
        envelope["sha256"] = digest(envelope["state"])
        path.write_bytes(canonical(envelope))
        with self.assertRaises(StateError):
            self.engine(create=False)

    def test_crash_after_journal_before_state_recovers_hard_latch(self):
        e = self.engine()
        original = e.store.atomic_write
        def crash(target, payload):
            if target.name == "state.json":
                raise OSError("injected disk failure after durable journal")
            original(target, payload)
        with patch.object(e.store, "atomic_write", side_effect=crash):
            with self.assertRaises(StateError):
                e.evaluate(snapshot(balance="500"), T)
        with self.assertRaises(StateError):
            e.evaluate(snapshot(cursor=2), T)
        e.close()
        resumed = self.engine(create=False)
        self.assertIn("absolute_floor", resumed.s["latches"])
        self.assertFalse((resumed.store.directory / "transaction.json").exists())
        self.assertFalse(resumed.boot_reconciled)
        self.assertFalse(resumed.evaluate(snapshot(cursor=2), T).entry_allowed)

    def test_action_claim_survives_restart_without_duplicate_dispatch(self):
        e = self.engine()
        s = snapshot(balance="400", positions=(position(),))
        action = e.evaluate(s, T).actions[0]
        e.claim_action(action["id"], T)
        e.close()
        resumed = self.engine(create=False)
        with self.assertRaises(StateError):
            resumed.claim_action(action["id"], T)
        resumed.evaluate(s, T)
        with self.assertRaises(StateError):
            resumed.claim_action(action["id"], T)
        s = self.renew(resumed, s, positions=(position(quantity="2"),), balance=D(430))
        result = resumed.evaluate(s, s.at)
        claimed = resumed.claim_action(result.actions[0]["id"], s.at)
        self.assertEqual(claimed["attempts"], 2)
        self.assertEqual(claimed["payload"]["remaining_quantity"], "2")

    def test_stale_or_missing_prices_still_allow_reconciled_risk_reduction(self):
        e = self.engine()
        s = snapshot(balance="400", positions=(position(),), orders=(order(),))
        first = e.evaluate(s, T)
        s = self.renew(e, s, quotes=())  # full equity unavailable, quantities/orders reconciled
        result = e.evaluate(s, s.at)
        self.assertFalse(result.entry_allowed)
        self.assertIsNone(result.equity)
        self.assertEqual({a["id"] for a in first.actions}, {a["id"] for a in result.actions})
        self.assertTrue(all(a["claimable"] for a in result.actions))
        for a in result.actions:
            e.claim_action(a["id"], s.at)
        s = self.renew(e, s, orders_at=s.at-timedelta(seconds=20))
        self.assertTrue(all(not a["claimable"] for a in e.evaluate(s, s.at).actions))

    def test_stale_price_without_hard_latch_still_cancels_entry(self):
        e = self.engine()
        s = snapshot(orders=(order(),))
        s = replace(s, quotes=tuple(replace(q, at=T-timedelta(seconds=16)) for q in s.quotes))
        result = e.evaluate(s, T)
        self.assertFalse(result.entry_allowed)
        self.assertEqual([a["kind"] for a in result.actions], ["CANCEL_ENTRY"])
        self.assertTrue(result.actions[0]["claimable"])

    def test_small_same_pair_pending_entry_cannot_split_trade_budget(self):
        e = self.engine()
        s = snapshot(orders=(order(quantity="1"),))
        allowed, reasons = e.assess_entry(s, entry(quantity=D(1)), T)
        self.assertFalse(allowed)
        self.assertIn("pending_entry_same_pair_cancel_and_reconcile_first", reasons)
        s = self.renew(e, s, orders=())
        self.assertTrue(e.assess_entry(s, replace(entry(quantity=D(1)), liquidity_at=s.at), s.at)[0])

    def test_action_age_is_input_age_not_time_of_repeated_evaluation(self):
        e = self.engine()
        s = snapshot(balance="400", positions=(position(),))
        result = e.evaluate(s, T+timedelta(seconds=14))
        with self.assertRaises(StateError):
            e.claim_action(result.actions[0]["id"], T+timedelta(seconds=16))
        with self.assertRaises(StateError):
            e.claim_action(result.actions[0]["id"], T-timedelta(seconds=1))

    def test_price_understatement_and_missing_entry_quote_fail_closed(self):
        e = self.engine()
        self.assertIn("trade_risk_including_roundtrip_cost", e.assess_entry(snapshot(), entry(price=D(1), quantity=D(10)), T)[1])
        e2 = self.engine(name="missingquote")
        self.assertFalse(e2.assess_entry(snapshot(quotes=()), entry(), T)[0])

    def test_futures_collateral_blocks_spending_but_does_not_reduce_equity(self):
        e = self.engine("futures")
        p = replace(position("futures", asset="ETH", quantity="1"), collateral=D(990))
        s = snapshot("futures", positions=(p,))
        self.assertEqual(e.equity(s), D(1000))
        self.assertIn("insufficient_unreserved_balance", e.assess_entry(s, entry("futures"), T)[1])

    def test_failed_multi_latch_review_cannot_partially_mutate_memory(self):
        e = self.engine()
        s = snapshot(balance="500")
        e.evaluate(s, T)
        s = self.renew(e, s, balance=D(880))
        e.evaluate(s, s.at)
        with self.assertRaises(StateError):
            e.review_recovery(s.at, authorization_id="review", incident_review="test", clear_hard=True, clear_warning=True)
        self.assertIn("absolute_floor", e.s["latches"])
        self.assertIn("hwm_hard_halt", e.s["latches"])

    def test_ambiguous_boolean_and_regressing_clock_fail_closed(self):
        e = self.engine()
        result = e.evaluate(snapshot(reconciliation_passed="yes"), T)
        self.assertFalse(result.entry_allowed)
        e2 = self.engine(name="clock")
        s = snapshot(balance="400", positions=(position(),))
        e2.evaluate(s, T)
        result = e2.evaluate(replace(s, cursor=2), T-timedelta(seconds=1))
        self.assertFalse(result.entry_allowed)
        self.assertTrue(all(not a["claimable"] for a in result.actions))

    def test_unknown_equity_does_not_dispatch_percentage_reduction_target(self):
        e = self.engine()
        s = snapshot(balance="900", day_start_equity=D(900), orders=(order(),))
        e.evaluate(s, T)
        s = self.renew(e, s, quotes=())
        result = e.evaluate(s, s.at)
        self.assertTrue(next(a for a in result.actions if a["kind"] == "CANCEL_ENTRY")["claimable"])
        self.assertFalse(next(a for a in result.actions if a["kind"] == "REDUCE_EXPOSURE")["claimable"])

    def test_no_balance_does_not_skip_safety_actions(self):
        e = self.engine()
        s = snapshot(balance="0", positions=(position(quantity="1"),))
        result = e.evaluate(s, T)
        self.assertIn("absolute_floor", result.reasons)
        self.assertEqual(result.actions[0]["kind"], "EXIT_POSITION")


if __name__ == "__main__":
    unittest.main(verbosity=2)
