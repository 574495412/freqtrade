"""Original metadata units,expiry arithmetic,native storage and no funded entry."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest
from bootstrap import HERE, boot


boot()
from freqtrade.data.history.inverse_options import (
    OptionChartContract,
    OptionChartFeatherHandler,
    load_option_chart_history,
    parse_option_chart,
)
from freqtrade.data.inverse_option_contracts import (
    OptionAdmissionError,
    isolated_expiry,
    option_trade_fee,
    parse_delivery_response,
    parse_instrument_response,
)
from freqtrade.enums import CandleType
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.option_backtesting import OptionBacktesting


D = Decimal
RECEIPTS = json.loads((HERE / "source_receipts.json").read_text())
META = next(r for r in RECEIPTS if r.get("path", "").endswith("/expired_instrument.json"))
CHART = next(r for r in RECEIPTS if r.get("path", "").endswith("/expired_option_candles.json"))


def metadata_response(**changes):
    from pathlib import Path

    response = json.loads(Path(META["path"]).read_text())
    response["result"].update(changes)
    return json.dumps(response).encode()


def parse(raw=None, url=None, status=200):
    return parse_instrument_response(
        metadata_response() if raw is None else raw,
        source_url=META["url"] if url is None else url,
        http_status=status,
        observed_at=datetime.fromisoformat(META["observed_at"]),
    )[0]


def spec():
    return parse()


def contract():
    first = datetime(2025, 6, 1, tzinfo=UTC)
    return OptionChartContract(spec(), tuple(first + timedelta(hours=i) for i in range(25)))


def chart(raw=None, url=None):
    from pathlib import Path

    return parse_option_chart(
        Path(CHART["path"]).read_bytes() if raw is None else raw,
        source_url=CHART["url"] if url is None else url,
        http_status=200,
        contract=contract(),
    )


def test_real_archived_metadata_keeps_usd_strike_and_coin_price_identity():
    s = spec()
    assert s.instrument_name == "BTC-27JUN25-100000-C" and s.strike_usd_per_base == D(100000)
    assert s.base_currency == "BTC" and s.min_amount_base == D(".1")
    assert s.storage_pair == "BTC-27JUN25-100000-C/BTC"


def test_lot_size_is_not_a_quantity_minimum_or_increment():
    s = parse(metadata_response(lot_size=99999))
    s.validate_known_order_limits(D(".1"), D(".005"))


def test_tick_threshold_is_strictly_above_not_greater_equal():
    s = spec()
    assert s.tick_for(D(".005")) == D(".0001")
    assert s.tick_for(D(".0051")) == D(".0005")
    s.validate_known_order_limits(D(".1"), D(".0055"))
    with pytest.raises(OptionAdmissionError):
        s.validate_known_order_limits(D(".1"), D(".0051"))


def test_fee_cap_is_premium_fraction_and_other_branch_underlying_fraction():
    s = spec()
    capped = option_trade_fee(
        s,
        amount_base=D(".1"),
        premium_base_per_base=D(".001"),
        underlying_rate=D(".0003"),
        premium_cap=D(".125"),
    )
    uncapped = option_trade_fee(
        s,
        amount_base=D(".1"),
        premium_base_per_base=D(".01"),
        underlying_rate=D(".0003"),
        premium_cap=D(".125"),
    )
    assert capped.currency == "BTC" and capped.amount == D(".0000125")
    assert uncapped.amount == D(".00003")


def test_official_isolated_call_example_is_coin_intrinsic_not_usd_profit():
    value = isolated_expiry(spec(), signed_amount_base=D(1), delivery_price_usd_per_base=D(125000))
    assert value.intrinsic_before_fees.currency == "BTC"
    assert value.intrinsic_before_fees.amount == D(".2") and value.route == "direct_coin"
    assert value.generated_futures_notional_usd == 0


def test_put_and_short_signs_and_current_dated_settlement_route():
    s = replace(
        spec(),
        instrument_name="BTC-1AUG26-100000-P",
        option_type="put",
        expiration_at=datetime(2026, 8, 1, 8, tzinfo=UTC),
    )
    long = isolated_expiry(s, signed_amount_base=D(1), delivery_price_usd_per_base=D(80000))
    short = isolated_expiry(s, signed_amount_base=D(-1), delivery_price_usd_per_base=D(80000))
    assert long.intrinsic_before_fees.amount == D(".25")
    assert long.generated_futures_notional_usd == -100000
    assert short.intrinsic_before_fees.amount == D("-.25")
    assert short.generated_futures_notional_usd == 100000
    assert long.route == "inverse_future_then_coin"


def test_out_of_money_has_no_generated_future_and_zero_intrinsic():
    s = replace(
        spec(),
        instrument_name="BTC-1AUG26-100000-C",
        expiration_at=datetime(2026, 8, 1, 8, tzinfo=UTC),
    )
    value = isolated_expiry(s, signed_amount_base=D(-1), delivery_price_usd_per_base=D(80000))
    assert value.intrinsic_before_fees.amount == 0 and value.generated_futures_notional_usd == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"strike": -1},
        {"contract_size": 10},
        {"quote_currency": "USD"},
        {"settlement_currency": "USDC"},
        {"counter_currency": "BTC"},
        {"instrument_type": "linear"},
        {"kind": "future"},
        {"option_type": "other"},
        {"instrument_name": "BTC-PERPETUAL"},
        {"instrument_id": True},
        {"expiration_timestamp": 1751011200001},
        {"creation_timestamp": 9999999999999},
        {"min_trade_amount": 0},
        {"tick_size_steps": [{"tick_size": 1}]},
        {
            "tick_size_steps": [
                {"above_price": 0.005, "tick_size": 0.0005},
                {"above_price": 0.005, "tick_size": 0.0005},
            ]
        },
    ],
)
def test_contract_unit_identity_and_grid_errors_rejected(changes):
    with pytest.raises(OptionAdmissionError):
        parse(metadata_response(**changes))


@pytest.mark.parametrize(
    "raw",
    [
        b"<html>403</html>",
        b"{}",
        b'{"jsonrpc":"2.0","error":{}}',
        b'{"jsonrpc":"2.0","result":{},"result":{}}',
    ],
)
def test_api_error_and_duplicate_json_never_becomes_metadata(raw):
    with pytest.raises(OptionAdmissionError):
        parse(raw)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": 403},
        {"url": META["url"].replace("BTC-27JUN25", "ETH-27JUN25")},
        {"url": META["url"].replace("www.deribit.com", "example.org")},
    ],
)
def test_original_request_must_match(kwargs):
    with pytest.raises(OptionAdmissionError):
        parse(**kwargs)


@pytest.mark.parametrize("value", [D("NaN"), D("Infinity"), 1.0, D(0), D(-1)])
def test_bad_delivery_prices_rejected(value):
    with pytest.raises(OptionAdmissionError):
        isolated_expiry(spec(), signed_amount_base=D(1), delivery_price_usd_per_base=value)


def test_native_storage_preserves_exact_25_rows_and_21_source_zero_volumes(tmp_path):
    c, frame = contract(), chart()
    assert len(frame) == 25 and int(frame.volume.eq(0).sum()) == 21
    c.validate(frame)
    handler = OptionChartFeatherHandler(tmp_path, c)
    handler.ohlcv_store(c.spec.storage_pair, "1h", frame, CandleType.SPOT)
    loaded = load_option_chart_history(tmp_path, c)
    pd.testing.assert_frame_equal(frame, loaded, check_dtype=False)
    assert int(loaded.volume.eq(0).sum()) == 21


@pytest.mark.parametrize(
    "change", ["duplicate", "missing", "negative", "ohlc", "no_data", "short_array"]
)
def test_bad_raw_chart_rejected_before_native_cleaning(change):
    from pathlib import Path

    response = json.loads(Path(CHART["path"]).read_text())
    row = response["result"]
    if change == "duplicate":
        row["ticks"][1] = row["ticks"][0]
    elif change == "missing":
        row["ticks"][1] += 3600000
    elif change == "negative":
        row["volume"][0] = -1
    elif change == "ohlc":
        row["low"][0] = 999
    elif change == "no_data":
        row["status"] = "no_data"
    elif change == "short_array":
        row["cost"].pop()
    with pytest.raises(OptionAdmissionError):
        chart(json.dumps(response).encode())


def test_request_window_and_option_identity_are_not_silently_trimmed_or_aliased():
    with pytest.raises(OptionAdmissionError):
        chart(url=CHART["url"].replace("resolution=60", "resolution=30"))
    with pytest.raises(OptionAdmissionError):
        chart(url=CHART["url"].replace("100000-C", "100000-P"))


def test_funded_option_entry_rejects_before_native_construction(monkeypatch):
    calls = []
    monkeypatch.setattr(Backtesting, "__init__", lambda *args, **kwargs: calls.append(True))
    with pytest.raises(OptionAdmissionError, match="No complete option return"):
        OptionBacktesting({})
    assert calls == []


def test_real_eth_lot10_does_not_override_minimum1():
    from pathlib import Path

    rec = next(r for r in RECEIPTS if r.get("path", "").endswith("/eth_instruments.json"))
    s = parse_instrument_response(
        Path(rec["path"]).read_bytes(),
        source_url=rec["url"],
        http_status=200,
        observed_at=datetime.fromisoformat(rec["observed_at"]),
    )[0]
    assert s.base_currency == "ETH" and s.min_amount_base == 1
    s.validate_known_order_limits(D(1), D(".005"))


def test_settlement_change_is_dated_and_caller_cannot_change_identity_only():
    before = replace(
        spec(),
        instrument_name="BTC-31JUL26-100000-C",
        expiration_at=datetime(2026, 7, 31, 8, tzinfo=UTC),
    )
    assert (
        isolated_expiry(
            before, signed_amount_base=D(1), delivery_price_usd_per_base=D(125000)
        ).route
        == "direct_coin"
    )
    with pytest.raises(OptionAdmissionError):
        replace(spec(), option_type="put")


@pytest.mark.parametrize("value", [True, "0.1", float("nan")])
def test_numeric_chart_types_not_silently_coerced(value):
    from pathlib import Path

    response = json.loads(Path(CHART["path"]).read_text())
    response["result"]["volume"][0] = value
    with pytest.raises(OptionAdmissionError):
        chart(json.dumps(response).encode())


def test_native_file_duplicates_rejected_before_native_aggregation(tmp_path):
    c, frame = contract(), chart()
    frame.loc[1, "date"] = frame.loc[0, "date"]
    handler = OptionChartFeatherHandler(tmp_path, c)
    handler.ohlcv_store(c.spec.storage_pair, "1h", frame, CandleType.SPOT)
    with pytest.raises(OptionAdmissionError):
        load_option_chart_history(tmp_path, c)


def delivery(raw=None, **kwargs):
    from pathlib import Path

    receipt = next(
        r
        for r in json.loads((HERE / "source_receipts.json").read_text())
        if r.get("path", "").endswith("/btc_expiry_delivery.json")
    )
    args = {
        "source_url": receipt["url"],
        "http_status": receipt["status"],
        "observed_at": datetime.fromisoformat(receipt["observed_at"]),
        "spec": spec(),
    }
    return parse_delivery_response(
        Path(receipt["path"]).read_bytes() if raw is None else raw, **(args | kwargs)
    )


def test_actual_delivery_price_keeps_index_and_expiry_identity():
    d = delivery()
    assert d.price_usd_per_base == D("107106.02")
    assert d.index_name == "btc_usd" and d.delivery_at == spec().expiration_at
    assert d.observed_at > d.delivery_at


@pytest.mark.parametrize(
    "row",
    [
        {"records_total": 2, "data": []},
        {"records_total": 1, "data": [{"date": "2025-06-28", "delivery_price": 1}]},
        {"records_total": 1, "data": [{"date": "2025-06-27", "delivery_price": 0}]},
        {"records_total": 1, "data": [{"date": "2025-06-27", "delivery_price": "107106.02"}]},
    ],
)
def test_invalid_or_other_day_delivery_is_not_filled_or_aliased(row):
    with pytest.raises(OptionAdmissionError):
        delivery(json.dumps({"jsonrpc": "2.0", "result": row}).encode())


def test_future_actual_delivery_cannot_be_observed_before_expiry():
    with pytest.raises(OptionAdmissionError):
        delivery(observed_at=spec().expiration_at - timedelta(seconds=1))
