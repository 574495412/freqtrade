"""Independent actual ZIP Decimal cash/funding audit; no candidate/engine import."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path
from zipfile import ZipFile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/joint_rules_native_20261117_v1"


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clock(value):
    return int(datetime.fromisoformat("2026-04-29T" + value + ":00+00:00").timestamp() * 1000)


def main():
    plan = read(HERE / "fixture_plan.json")
    terminals = read(HERE / "execution_complete.json")
    assert len(terminals["cases"]) == 5
    for path, digest in plan["source_hashes"].items():
        assert sha(path) == digest, path
    records = []
    for case in plan["cases"]:
        for path, digest in case["inputs"].items():
            assert sha(path) == digest, path
        name = case["name"]
        folder = OUT / "runs" / name
        producer = read(folder / "producer_receipt.json")
        assert producer["source_hashes"] == plan["source_hashes"]
        assert producer["plan_sha256"] == sha(HERE / "fixture_plan.json")
        terminal = read(folder / "process_receipt.json")
        paths = list((folder / "native_results").glob("*.zip"))
        if case["expected"] == "rejected":
            failure = read(folder / "failure.json")
            assert terminal["returncode"] != 0 and not paths
            assert not failure["native_start_completed"]
            if name == "unknown_amount":
                assert not failure["native_start_attempted"]
                assert "Incomplete known amount rule coverage" in failure["error"]
                assert not failure["open_trades"]
            elif name == "unknown_pending":
                assert failure["native_start_attempted"]
                assert "Unknown pending matching policy" in failure["error"]
                one = next(t for t in failure["open_trades"] if t["pair"].startswith("FAKEONE"))
                assert D(str(one["amount"])) == D("1.6")
            else:
                assert failure["native_start_attempted"]
                assert "Unsupported dated close amount" in failure["error"]
                one = next(t for t in failure["open_trades"] if t["pair"].startswith("FAKEONE"))
                assert D(str(one["amount"])) == D("0.4")
            records.append(
                dict(
                    case=name,
                    expected_rejected=True,
                    error=failure["error"],
                    native_start_attempted=failure["native_start_attempted"],
                    open_trades_preserved=len(failure["open_trades"]),
                    failure_sha256=sha(folder / "failure.json"),
                )
            )
            continue
        assert terminal["returncode"] == 0 and len(paths) == 1
        with ZipFile(paths[0]) as z:
            assert z.testzip() is None
            payloads = [json.loads(z.read(n)) for n in z.namelist() if n.endswith(".json")]
        result = read(folder / "result.json")
        native = next(p["strategy"]["FixtureJoint"] for p in payloads if "strategy" in p)
        assert len(native["trades"]) == 2 and result["open_trades"] == 0
        assert result["native_start_completed"]
        joint = read(folder / "joint_policy.json")
        assert joint["mro"][:4] == [
            "NativeJoint",
            "DatedAmountMixin",
            "OrderOperationMixin",
            "DatedPricePrecisionMixin",
        ]
        assert joint["source_hashes"] == plan["source_hashes"]
        pnl = D(0)
        errors, rows = [], []
        for trade in native["trades"]:
            assert not trade["is_short"] and trade["leverage"] == 1
            assert trade["fee_open"] == trade["fee_close"] == 0.0015
            orders = trade["orders"]
            quantities = [D(str(o["amount"])) for o in orders]
            timestamps = [o["order_filled_timestamp"] for o in orders]
            one = trade["pair"].startswith("FAKEONE")
            assert quantities == (
                [D(1), D("0.6"), D("1.2"), D("0.4")] if one else [D("0.5"), D("0.5")]
            )
            assert timestamps == (
                [
                    clock("05:40"),
                    clock("05:51"),
                    clock("06:01" if name == "continue" else "06:03"),
                    clock("06:10"),
                ]
                if one
                else [clock("06:03"), clock("06:10")]
            )
            assert [D(str(o["safe_price"])) for o in orders] == (
                [D(100), D(100), D("103.005"), D(100)] if one else [D("100.01"), D(100)]
            )
            buys = sells = inventory = funding_quantity = D(0)
            for order in orders:
                quantity, price = D(str(order["amount"])), D(str(order["safe_price"]))
                if order["ft_is_entry"]:
                    buys += quantity * price
                    inventory += quantity
                else:
                    sells += quantity * price
                    inventory -= quantity
                assert inventory >= 0
                # No test fill is coincident with06:00; no ambiguous fee ordering.
                assert order["order_filled_timestamp"] != clock("06:00")
                if order["order_filled_timestamp"] < clock("06:00"):
                    funding_quantity += quantity if order["ft_is_entry"] else -quantity
            assert inventory == 0
            funding = -funding_quantity * D(100) * D("0.001")
            assert abs(funding - D(str(trade["funding_fees"]))) < D("1e-12")
            expected = sells - buys - (buys + sells) * D("0.0015") + funding
            errors.append(abs(expected - D(str(trade["profit_abs"]))))
            pnl += expected
            rows.append(
                dict(
                    pair=trade["pair"],
                    quantities=list(map(str, quantities)),
                    fills=timestamps,
                    funding_usdt=str(funding),
                    pnl_usdt=str(expected),
                )
            )
        errors.extend(
            [
                abs(D(str(result["native_wallet_final"])) - D(1000) - pnl),
                abs(D(str(native["final_balance"])) - D(1000) - pnl),
            ]
        )
        assert all(x < D("1e-7") for x in errors), errors
        actions = read(folder / "operation_trace.json")
        blocked = [
            x for x in actions if x["pair"].startswith("FAKETWO") and x["action"] == "new_entry"
        ]
        assert blocked
        assert all(
            clock("06:00")
            <= int(datetime.fromisoformat(x["at"]).timestamp() * 1000)
            < clock("06:03")
            for x in blocked
        )
        amount_trace = read(folder / "amount_trace.json")
        assert any(
            x["accepted"] and x["is_exit"] and D(str(x["quantity"])) == D("0.4") for x in amount_trace
        )
        records.append(
            dict(
                case=name,
                completed=True,
                native_zip=str(paths[0]),
                native_zip_sha256=sha(paths[0]),
                trades=rows,
                final_wallet=str(D(1000) + pnl),
                maximum_cash_error_usdt=str(max(errors)),
                blocked_new_entry_calls=len(blocked),
                old_pending_price_preserved=True,
                below_opening_notional_remainder_closed=True,
                synthetic_only=True,
            )
        )
    manifest = read(ROOT / "deploy/upstream-source.json")
    for rel, digest in manifest["files"].items():
        assert sha(ROOT.parent / "CZ-Agent/vendor/freqtrade" / rel) == digest
    verification = dict(
        verified_at=datetime.now(UTC).isoformat(),
        passed=True,
        combined_mro_native_completed=2,
        actual_zip_cash_audits_passed=2,
        expected_rejected_cases=3,
        official_vendor_files_unchanged=len(manifest["files"]),
        local_native_modules_unchanged=True,
        evidence=records,
        synthetic_only=True,
        new_historical_strategy_accounts=0,
        corrected_NFI_returns=None,
        full40_historical_rules_admitted=False,
        orders_enabled=False,
        goal_complete=False,
    )
    (HERE / "verification.json").write_text(
        json.dumps(verification, indent=2, allow_nan=False) + "\n"
    )
    print(
        "VERIFIED 2 complete joint-native ZIPs and3expected rejects; no historical strategy returns"
    )


if __name__ == "__main__":
    main()
