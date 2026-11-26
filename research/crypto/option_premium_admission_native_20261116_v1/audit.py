"""Independent source/metadata/history/Fraction audit;no candidate imports."""

import hashlib
import json
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import pandas as pd


HERE = Path(__file__).absolute().parent


def main():
    receipts = json.loads((HERE / "source_receipts.json").read_text())
    for row in receipts:
        if "path" in row:
            assert hashlib.sha256(Path(row["path"]).read_bytes()).hexdigest() == row["sha256"]
    admission = json.loads((HERE / "metadata_admission.json").read_text())
    pairs = []
    for filename in ("btc_instruments.json", "eth_instruments.json", "expired_instrument.json"):
        raw = json.loads((HERE / "sources" / filename).read_text())
        records = raw["result"] if isinstance(raw["result"], list) else [raw["result"]]
        assert len(records) == admission["counts"][filename]
        pairs.extend(records)
    by_id = {r["instrument_id"]: r for r in pairs}
    assert len(by_id) == len(pairs)
    for spec in admission["specs"]:
        raw = by_id[spec["instrument_id"]]
        assert spec["instrument_name"] == raw["instrument_name"]
        assert raw["base_currency"] == raw["quote_currency"] == raw["settlement_currency"]
        assert raw["counter_currency"] == "USD" and raw["contract_size"] == 1
        assert Fraction(spec["strike_usd_per_base"]) == Fraction(str(raw["strike"]))
        assert Fraction(spec["min_amount_base"]) == Fraction(str(raw["min_trade_amount"]))
    original = json.loads((HERE / "sources/expired_option_candles.json").read_text())["result"]
    native = pd.read_csv(HERE / "native_chart.csv", float_precision="round_trip")
    assert len(original["ticks"]) == len(native) == 25
    assert [int(pd.Timestamp(x).timestamp() * 1000) for x in native.date] == original["ticks"]
    scalar_checks = 0
    for key in ("open", "high", "low", "close", "volume"):
        assert native[key].tolist() == original[key]
        scalar_checks += len(native)
    probe = json.loads((HERE / "unit_probe.json").read_text())
    for row in probe["expiry_cases"]:
        amount, price, strike = [
            Fraction(row[k])
            for k in ("signed_amount_base", "delivery_price_usd_per_base", "strike_usd_per_base")
        ]
        change = price - strike if row["option_type"] == "call" else strike - price
        intrinsic = max(change, Fraction(0))
        assert (
            Fraction(row["output"]["intrinsic_before_fees"]["amount"]) == amount * intrinsic / price
        )
        assert row["output"]["intrinsic_before_fees"]["currency"] == "BTC"
        physical = datetime.fromisoformat(row["expiry"]).date().isoformat() >= "2026-08-01"
        assert row["output"]["route"] == ("inverse_future_then_coin" if physical else "direct_coin")
        generated = (
            amount * strike * (1 if row["option_type"] == "call" else -1)
            if physical and intrinsic
            else 0
        )
        assert Fraction(row["output"]["generated_futures_notional_usd"]) == generated
        scalar_checks += 4
    for row in probe["fee_cases"]:
        amount, price, rate, cap = [
            Fraction(row[k])
            for k in ("amount_base", "premium_base_per_base", "underlying_rate", "premium_cap")
        ]
        assert Fraction(row["output"]["amount"]) == amount * min(rate, price * cap)
        scalar_checks += 1
    delivery = json.loads((HERE / "actual_delivery_admission.json").read_text())
    original_delivery = json.loads(Path(delivery["original_source_path"]).read_text())["result"]
    assert original_delivery["records_total"] == 1 and len(original_delivery["data"]) == 1
    assert original_delivery["data"][0]["date"] == "2025-06-27"
    actual_price = Fraction(str(original_delivery["data"][0]["delivery_price"]))
    assert Fraction(delivery["delivery"]["price_usd_per_base"]) == actual_price
    expected = (actual_price - 100000) / actual_price
    actual = Fraction(
        delivery["hypothetical_one_base_unit_intrinsic"]["intrinsic_before_fees"]["amount"]
    )
    assert abs(actual - expected) < Fraction(1, 10**27)
    scalar_checks += 3
    result = {
        "passed": True,
        "metadata_records_independently_checked": len(pairs),
        "source_chart_and_unit_scalars_checked": scalar_checks,
        "source_zero_volume_rows": sum(v == 0 for v in original["volume"]),
        "candidate_module_imported": False,
        "financial_account_reconstructed": False,
        "evidence_tier": "Input/isolated arithmetic only;not full account economics",
    }
    (HERE / "independent_audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
