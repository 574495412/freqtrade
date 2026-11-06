"""Synthetic protocol fixtures. No authenticated account or financial run."""

import copy
import unittest
from decimal import Decimal

from receipt_intake import CONTRACT, clock_text, normalize_batch


START = 1788268800000
PAIRS = {"BTCUSDT": "BTC/USDT:USDT", "ETHUSDT": "ETH/USDT:USDT"}


def trade(sequence, side="BUY", qty="0.2", cumulative="0.2", requested="0.2", order_id=10):
    at = START + 3 * sequence
    price, pnl = ("100", "0") if side == "BUY" else ("105", "1")
    return {
        "sequence": sequence,
        "campaign_id": "btc-1",
        "received_at": clock_text(at + 2),
        "ws": {
            "e": "ORDER_TRADE_UPDATE",
            "T": at,
            "E": at + 1,
            "o": {
                "s": "BTCUSDT",
                "c": f"client-{order_id}",
                "o": "LIMIT",
                "S": side,
                "ps": "BOTH",
                "i": order_id,
                "t": 50 + sequence,
                "x": "TRADE",
                "X": "FILLED" if cumulative == requested else "PARTIALLY_FILLED",
                "q": requested,
                "l": qty,
                "z": cumulative,
                "L": price,
                "n": "0.01",
                "N": "USDT",
                "m": True,
                "rp": pnl,
                "T": at,
            },
        },
        "rest": {
            "symbol": "BTCUSDT",
            "orderId": order_id,
            "id": 50 + sequence,
            "time": at,
            "side": side,
            "positionSide": "BOTH",
            "qty": qty,
            "price": price,
            "commission": "0.010000",
            "commissionAsset": "USDT",
            "marginAsset": "USDT",
            "maker": True,
            "realizedPnl": pnl,
        },
    }


def funding(sequence, income="-0.10", transaction=900):
    at = START + 3 * sequence
    return {
        "sequence": sequence,
        "campaign_id": "btc-1",
        "received_at": clock_text(at + 2),
        "ws": {
            "e": "ACCOUNT_UPDATE",
            "T": at,
            "E": at + 1,
            "a": {
                "m": "FUNDING_FEE",
                "S": "BTCUSDT",
                "B": [{"a": "USDT", "bc": income}],
                "P": [{"s": "BTCUSDT", "mt": "isolated", "ps": "BOTH"}],
            },
        },
        "rest": {
            "symbol": "BTCUSDT",
            "asset": "USDT",
            "time": at,
            "incomeType": "FUNDING_FEE",
            "income": income,
            "tranId": transaction,
            "tradeId": "",
        },
    }


def bundle(*rows):
    return {
        "contract": CONTRACT,
        "evidence_class": "synthetic",
        "account_id": "redacted-account",
        "connection_id": "redacted-connection",
        "financial_receipts": list(rows),
    }


def normalize(*rows):
    return normalize_batch(bundle(*rows), pairs=PAIRS)


class ReceiptIntakeTests(unittest.TestCase):
    def test_full_round_trip_ledger_and_closed_campaign_funding(self):
        from account_replay import AccountReplay

        first = trade(1, qty="0.1", cumulative="0.1")
        final = trade(2, qty="0.1")
        close = trade(4, side="SELL", order_id=11)
        data = bundle(first, final, funding(3), close, funding(5, "0.04", 901))
        original = copy.deepcopy(data)
        output = normalize_batch(data, pairs=PAIRS)
        self.assertEqual(data, original)
        self.assertEqual(output, normalize_batch(data, pairs=PAIRS))
        self.assertEqual(output["events"][0]["published_at"], clock_text(START + 4))
        self.assertEqual(output["events"][0]["received_at"], clock_text(START + 5))
        self.assertEqual(output["events"][0]["event_at"], clock_text(START + 3))
        replay = AccountReplay(
            output["market"],
            sequence_basis=output["sequence_basis"],
            evidence_class=output["evidence_class"],
        )
        for event in output["events"]:
            self.assertTrue(replay.apply(event))
        self.assertEqual(replay.positions, {})
        self.assertEqual(replay.realized, Decimal(1))
        self.assertEqual(replay.fees, Decimal("0.03"))
        self.assertEqual(replay.funding_cash, Decimal("-0.06"))
        self.assertEqual(replay.wallet, Decimal("1000.91"))
        self.assertEqual(set(replay.settled_funding), {"funding:900", "funding:901"})

    def test_declared_captured_class_does_not_promote_evidence(self):
        data = bundle(trade(1))
        data["evidence_class"] = "captured"
        output = normalize_batch(data, pairs=PAIRS)
        for key in (
            "source_authentication_verified",
            "complete_from_inception",
            "economic_qualified",
        ):
            self.assertFalse(output[key])
        self.assertEqual(output["sequence_basis"], "unknown")

    def test_same_millisecond_funding_and_fill_rejected_even_with_publication_order(self):
        event = funding(2)
        event["rest"]["time"] = event["ws"]["T"] = START + 3
        with self.assertRaisesRegex(ValueError, "same-millisecond financial order unproven"):
            normalize(trade(1), event)

    def test_same_millisecond_fills_rejected(self):
        event = trade(2)
        event["rest"]["time"] = event["ws"]["T"] = event["ws"]["o"]["T"] = START + 3
        with self.assertRaisesRegex(ValueError, "same-millisecond financial order unproven"):
            normalize(trade(1), event)

    def test_order_trade_clock_must_match_rest_and_outer_transaction(self):
        event = trade(1)
        event["rest"]["time"] = event["ws"]["o"]["T"] = START + 2
        with self.assertRaisesRegex(ValueError, "fill conflict"):
            normalize(event)

    def test_missing_receipt_clock_and_publication_after_receipt_fail(self):
        event = trade(1)
        del event["received_at"]
        with self.assertRaisesRegex(ValueError, "required field"):
            normalize(event)
        event = trade(1)
        event["ws"]["E"] += 2
        with self.assertRaisesRegex(ValueError, "source clocks"):
            normalize(event)

    def test_capture_does_not_sort_regressions_or_repair_gaps(self):
        first = trade(1)
        first["received_at"] = clock_text(START + 10)
        with self.assertRaisesRegex(ValueError, "time regressed"):
            normalize(first, funding(2))
        with self.assertRaisesRegex(ValueError, "sequence has a gap"):
            normalize(trade(1), funding(3))
        later = funding(2)
        later["rest"]["time"] = later["ws"]["T"] = START + 2
        with self.assertRaisesRegex(ValueError, "time regressed"):
            normalize(trade(1), later)

    def test_same_type_publication_cannot_regress(self):
        first = trade(1)
        first["received_at"] = clock_text(START + 7)
        first["ws"]["E"] = START + 7
        with self.assertRaisesRegex(ValueError, "publication time did not advance"):
            normalize(first, trade(2, order_id=11))

    def test_rest_fill_fields_must_match_exactly(self):
        changes = {
            "symbol": "ETHUSDT",
            "qty": "0.200000001",
            "price": "100.01",
            "commission": "0.011",
            "realizedPnl": "0.001",
            "maker": 1,
            "id": 999,
            "orderId": 999,
            "side": "SELL",
        }
        for key, value in changes.items():
            with self.subTest(key=key):
                event = trade(1)
                event["rest"][key] = value
                with self.assertRaisesRegex(ValueError, "fill conflict"):
                    normalize(event)

    def test_non_quote_commission_and_float_fee_rejected(self):
        event = trade(1)
        event["rest"]["commissionAsset"] = event["ws"]["o"]["N"] = "BNB"
        with self.assertRaisesRegex(ValueError, "USDT commission"):
            normalize(event)
        event = trade(1)
        event["rest"]["commission"] = 0.01
        with self.assertRaisesRegex(ValueError, "decimal string"):
            normalize(event)

    def test_missing_partial_and_bad_terminal_status_rejected(self):
        with self.assertRaisesRegex(ValueError, "fill chain"):
            normalize(trade(1, qty="0.1"))
        event = trade(1)
        event["ws"]["o"]["X"] = "PARTIALLY_FILLED"
        with self.assertRaisesRegex(ValueError, "fill chain"):
            normalize(event)

    def test_missing_or_conflicting_funding_identity_or_receipt(self):
        event = funding(2)
        del event["rest"]["tranId"]
        with self.assertRaisesRegex(ValueError, "required field"):
            normalize(trade(1), event)
        event = funding(2)
        event["rest"]["income"] = "0.1"
        with self.assertRaisesRegex(ValueError, "funding conflict"):
            normalize(trade(1), event)
        event = funding(2)
        event["ws"]["a"]["S"] = "ETHUSDT"
        with self.assertRaisesRegex(ValueError, "funding conflict"):
            normalize(trade(1), event)

    def test_cross_and_multi_asset_funding_are_outside_contract(self):
        event = funding(2)
        event["ws"]["a"]["P"] = []
        with self.assertRaisesRegex(ValueError, "single-asset isolated"):
            normalize(trade(1), event)
        event = funding(2)
        event["ws"]["a"]["B"].append({"a": "BNB", "bc": "0"})
        with self.assertRaisesRegex(ValueError, "single-asset isolated"):
            normalize(trade(1), event)

    def test_duplicate_trade_and_funding_exchange_ids_rejected(self):
        event = trade(2, order_id=12)
        event["rest"]["id"] = event["ws"]["o"]["t"] = 51
        with self.assertRaisesRegex(ValueError, "duplicate exchange identity"):
            normalize(trade(1), event)
        with self.assertRaisesRegex(ValueError, "duplicate exchange identity"):
            normalize(trade(1), funding(2), funding(3))

    def test_inventory_and_campaign_binding(self):
        with self.assertRaisesRegex(ValueError, "long inventory"):
            normalize(trade(1, side="SELL"))
        event = funding(2)
        event["campaign_id"] = "unseen"
        with self.assertRaisesRegex(ValueError, "no matched entry"):
            normalize(trade(1), event)
        event = trade(3, order_id=12)
        with self.assertRaisesRegex(ValueError, "campaign binding"):
            normalize(trade(1), trade(2, side="SELL", order_id=11), event)

    def test_unhandled_financial_events_are_not_dropped(self):
        event = funding(2)
        event["rest"]["incomeType"] = event["ws"]["a"]["m"] = "TRANSFER"
        with self.assertRaisesRegex(ValueError, "unsupported balance"):
            normalize(trade(1), event)
        event = trade(1)
        event["ws"]["o"]["x"] = "CALCULATED"
        with self.assertRaisesRegex(ValueError, "unsupported order"):
            normalize(event)
        event = trade(1)
        event["ws"]["o"]["c"] = "autoclose-123"
        with self.assertRaisesRegex(ValueError, "liquidation"):
            normalize(event)

    def test_single_connection_limit_and_explicit_frozen_pairs(self):
        event = funding(2)
        event["received_at"] = clock_text(START + 5 + 24 * 3600 * 1000)
        with self.assertRaisesRegex(ValueError, "single connection exceeds"):
            normalize(trade(1), event)
        with self.assertRaisesRegex(ValueError, "frozen symbol absent"):
            normalize_batch(bundle(trade(1)), pairs={})
        with self.assertRaisesRegex(ValueError, "explicit USD-M symbol"):
            normalize_batch(bundle(trade(1)), pairs={"BTCUSDT": PAIRS["ETHUSDT"]})
