"""Actual current/archived API inputs and native history;no option account."""

import hashlib
import inspect
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from bootstrap import HERE, boot


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def encode(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def save(name, value):
    (HERE / name).write_text(json.dumps(value, default=encode, indent=2) + "\n")


def main():
    engine = boot()
    from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
    from freqtrade.data.history.history_utils import load_pair_history
    from freqtrade.data.history.inverse_options import (
        OptionChartContract,
        OptionChartFeatherHandler,
        load_option_chart_history,
        parse_option_chart,
    )
    from freqtrade.data.inverse_option_contracts import (
        isolated_expiry,
        option_trade_fee,
        parse_delivery_response,
        parse_instrument_response,
    )
    from freqtrade.enums import CandleType

    receipts = json.loads((HERE / "source_receipts.json").read_text())
    for row in receipts:
        if "path" in row:
            assert sha(row["path"]) == row["sha256"], row["path"]
    specs, counts, archived = [], {}, None
    for name in ("btc_instruments.json", "eth_instruments.json", "expired_instrument.json"):
        row = next(r for r in receipts if r.get("path", "").endswith("/" + name))
        admitted = parse_instrument_response(
            Path(row["path"]).read_bytes(),
            source_url=row["url"],
            http_status=row["status"],
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )
        counts[name] = len(admitted)
        specs.extend(asdict(s) for s in admitted)
        if name == "expired_instrument.json":
            archived = admitted[0]
    save(
        "metadata_admission.json",
        {
            "counts": counts,
            "specs": specs,
            "metadata_is_current_capture": True,
            "historical_point_in_time_universe_certified": False,
        },
    )
    first = datetime(2025, 6, 1, tzinfo=UTC)
    contract = OptionChartContract(archived, tuple(first + timedelta(hours=i) for i in range(25)))
    row = next(r for r in receipts if r.get("path", "").endswith("/expired_option_candles.json"))
    frame = parse_option_chart(
        Path(row["path"]).read_bytes(),
        source_url=row["url"],
        http_status=row["status"],
        contract=contract,
    )
    folder = HERE / "native_history"
    folder.mkdir(exist_ok=True)
    OptionChartFeatherHandler(folder, contract).ohlcv_store(
        archived.storage_pair, "1h", frame, CandleType.SPOT
    )
    native = load_option_chart_history(folder, contract)
    assert frame.equals(native)
    native.to_csv(HERE / "native_chart.csv", index=False)
    save(
        "native_history_admission.json",
        {
            "engine": engine,
            "actual_native_handler": inspect.getfile(FeatherDataHandler),
            "actual_native_loader": inspect.getfile(load_pair_history),
            "local_storage_key": archived.storage_pair,
            "rows": len(native),
            "original_zero_volume_rows": int(frame.volume.eq(0).sum()),
            "native_zero_volume_rows": int(native.volume.eq(0).sum()),
            "native_history_exactly_equals_source": True,
            "native_added_rows": 0,
            "candle_type_spot_is_storage_namespace_only": True,
            "full_strategy_window_covered": False,
            "historical_bid_ask_available": False,
            "chart_prices_are_not_certified_executable": True,
        },
    )
    row = next(r for r in receipts if r.get("path", "").endswith("/btc_expiry_delivery.json"))
    delivery = parse_delivery_response(
        Path(row["path"]).read_bytes(),
        source_url=row["url"],
        http_status=row["status"],
        observed_at=datetime.fromisoformat(row["observed_at"]),
        spec=archived,
    )
    hypothetical_unit_intrinsic = isolated_expiry(
        archived,
        signed_amount_base=Decimal(1),
        delivery_price_usd_per_base=delivery.price_usd_per_base,
    )
    save(
        "actual_delivery_admission.json",
        {
            "delivery": asdict(delivery),
            "original_source_path": row["path"],
            "hypothetical_one_base_unit_intrinsic": asdict(hypothetical_unit_intrinsic),
            "actual_position_or_filled_order": False,
            "premium_or_hedge_cash_known": False,
            "financial_account_return": None,
        },
    )
    cases = []
    for kind in ("call", "put"):
        for expiry in (datetime(2025, 6, 27, 8, tzinfo=UTC), datetime(2026, 8, 1, 8, tzinfo=UTC)):
            label = "27JUN25" if expiry.year == 2025 else "1AUG26"
            s = replace(
                archived,
                option_type=kind,
                expiration_at=expiry,
                instrument_name=f"BTC-{label}-100000-{'C' if kind == 'call' else 'P'}",
            )
            for amount in (Decimal(1), Decimal(-1)):
                price = Decimal(125000 if kind == "call" else 80000)
                value = isolated_expiry(
                    s, signed_amount_base=amount, delivery_price_usd_per_base=price
                )
                cases.append(
                    {
                        "option_type": kind,
                        "expiry": expiry,
                        "signed_amount_base": amount,
                        "strike_usd_per_base": s.strike_usd_per_base,
                        "delivery_price_usd_per_base": price,
                        "output": asdict(value),
                    }
                )
    fees = []
    for premium in (Decimal(".001"), Decimal(".01")):
        value = option_trade_fee(
            archived,
            amount_base=Decimal(".1"),
            premium_base_per_base=premium,
            underlying_rate=Decimal(".0003"),
            premium_cap=Decimal(".125"),
        )
        fees.append(
            {
                "amount_base": ".1",
                "premium_base_per_base": premium,
                "underlying_rate": ".0003",
                "premium_cap": ".125",
                "output": asdict(value),
            }
        )
    save(
        "unit_probe.json",
        {
            "scope": "Synthetic arithmetic using real admitted contract units;not account returns",
            "expiry_cases": cases,
            "fee_cases": fees,
            "funded_accounts": 0,
        },
    )
    print(
        json.dumps(
            {
                "engine": engine,
                "metadata": counts,
                "native_chart_rows": len(native),
                "zero_volume_rows": int(native.volume.eq(0).sum()),
                "account_runs": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
