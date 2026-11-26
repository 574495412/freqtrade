"""Input-unit/revision boundaries and actual inherited native callback clocks."""

import hashlib
import importlib.util
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pandas as pd
import pytest
from bootstrap import ROOT, boot


boot()
from freqtrade.data.equity_provider import EquitySchedule
from freqtrade.data.equity_split_notices import SplitNotice, SplitNoticeBook, SplitSource
from freqtrade.data.equity_split_notices_provider import EquitySplitNoticeDataProvider
from freqtrade.data.history.equity import EquityAdmissionError, EquitySessionContract
from freqtrade.enums import RunMode


PUB = datetime(2025, 3, 13, 12, tzinfo=UTC)
SEEN = PUB + timedelta(minutes=1)
RAW = b"SYNTHETIC SOURCE: conditional 15-for-1 split announcement; not an issuer document"
SOURCE = SplitSource(RAW, "https://example.org/synthetic", hashlib.sha256(RAW).hexdigest(), 200)
OPEN = datetime(2025, 6, 10, 13, 30, tzinfo=UTC)


def notice(**kwargs):
    return SplitNotice(
        **(
            {
                "event_id": "split2025",
                "revision_id": "r1",
                "security_id": "CIK0000898173-common",
                "pair": "ORLY/USD",
                "calendar": "XNYS",
                "state": "proposed",
                "published_at": PUB,
                "observed_at": SEEN,
                "source": SOURCE,
                "old_shares": 1,
                "new_shares": 15,
                "planned_trade_open": OPEN,
            }
            | kwargs
        )
    )


def provider(notices=None, pair="ORLY/USD", calendar="XNYS"):
    labels = ("2025-03-13", "2025-03-14")
    dates = pd.to_datetime(labels, utc=True)
    schedule = pd.DataFrame(
        {
            "open": dates + pd.Timedelta(hours=13.5),
            "close": dates + pd.Timedelta(hours=20),
            "break_start": pd.NaT,
            "break_end": pd.NaT,
        },
        index=dates,
    )
    contract = EquitySessionContract(pair, calendar, labels)
    return EquitySplitNoticeDataProvider(
        {"runmode": RunMode.BACKTEST, "timeframe": "1d"},
        {pair: EquitySchedule(contract, schedule)},
        SplitNoticeBook((notice(),) if notices is None else notices),
    )


def lookup(book, at=SEEN, **kwargs):
    return book.lookup("ORLY/USD", "CIK0000898173-common", "split2025", at=at, **kwargs)


def test_exact_reciprocal_units_and_proposal_is_not_execution():
    value = lookup(SplitNoticeBook((notice(),)))
    assert value["price_factor"] == Fraction(1, 15)
    assert value["quantity_factor"] == Fraction(15, 1)
    assert value["notice_state"] == "proposed" and value["direction"] == "forward"
    assert value["execution_confirmed"] is None


def test_reverse_split_keeps_reciprocal_units_without_short_order():
    value = lookup(SplitNoticeBook((notice(old_shares=10, new_shares=1),)))
    assert value["price_factor"] == 10 and value["quantity_factor"] == Fraction(1, 10)
    assert value["direction"] == "reverse" and "order" not in value


@pytest.mark.parametrize("field", ["published_at", "observed_at"])
def test_missing_clock_cannot_expose_terms(field):
    assert lookup(SplitNoticeBook((notice(**{field: None}),)))["price_factor"] is None


def test_observed_now_never_backfills_historical_signal():
    n = notice(observed_at=datetime(2026, 11, 16, tzinfo=UTC))
    assert lookup(SplitNoticeBook((n,)))["status"] == "unknown"


def test_event_absence_and_future_event_look_identical():
    empty = lookup(SplitNoticeBook(()), PUB)
    future = lookup(SplitNoticeBook((notice(),)), PUB)
    assert empty == future and empty["old_shares"] is None


def test_last_published_visible_revision_beats_late_arriving_older_notice():
    first = notice(observed_at=SEEN + timedelta(days=4))
    newer = notice(
        revision_id="r2",
        published_at=PUB + timedelta(days=1),
        observed_at=SEEN + timedelta(days=1),
        new_shares=20,
    )
    book = SplitNoticeBook((first, newer))
    assert lookup(book, SEEN + timedelta(days=2))["new_shares"] == 20
    assert lookup(book, SEEN + timedelta(days=5))["new_shares"] == 20


def test_cancellation_stops_known_terms_only_when_visible():
    cancelled = notice(
        revision_id="r2",
        state="cancelled",
        old_shares=None,
        new_shares=None,
        planned_trade_open=None,
        published_at=PUB + timedelta(days=1),
        observed_at=SEEN + timedelta(days=1),
    )
    book = SplitNoticeBook((notice(), cancelled))
    assert lookup(book)["new_shares"] == 15
    value = lookup(book, SEEN + timedelta(days=1))
    assert value["notice_state"] == "cancelled" and value["price_factor"] is None


def test_record_distribution_and_planned_trade_clock_are_not_interchangeable():
    record = datetime(2025, 6, 2, 20, tzinfo=UTC)
    distribution = datetime(2025, 6, 9, 20, tzinfo=UTC)
    n = notice(record_at=record, distribution_at=distribution)
    value = lookup(SplitNoticeBook((n,)), OPEN + timedelta(days=1))
    assert value["record_at"] == record and value["distribution_at"] == distribution
    assert value["planned_trade_open"] == OPEN and value["execution_confirmed"] is None
    unknown = lookup(SplitNoticeBook((replace(n, planned_trade_open=None),)))
    assert unknown["planned_trade_open"] is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"old_shares": 0},
        {"new_shares": -1},
        {"old_shares": True},
        {"new_shares": 15.0},
        {"new_shares": None},
        {"new_shares": 1},
        {"old_shares": Fraction(1)},
        {"state": "effective"},
        {"pair": "ORLY/HKD"},
        {"calendar": "XNAS"},
        {"security_id": ""},
        {"state": "cancelled"},
        {"published_at": datetime(2025, 3, 13)},  # noqa: DTZ001 - invalid-clock fixture
        {"observed_at": PUB - timedelta(seconds=1)},
        {"published_at": pd.NaT},
        {"planned_trade_open": datetime(2025, 6, 10)},  # noqa: DTZ001 - invalid-clock fixture
    ],
)
def test_invalid_terms_and_ambiguous_state_rejected(kwargs):
    with pytest.raises(EquityAdmissionError):
        notice(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"raw": b""},
        {"sha256": "0" * 64},
        {"http_status": 403},
        {"url": "http://example.org/a"},
        {"url": "https://user:pass@example.org/a"},
    ],
)
def test_original_byte_receipt_consistency(kwargs):
    with pytest.raises(EquityAdmissionError):
        replace(SOURCE, **kwargs)


def test_exact_class_identity_no_alias_or_old_event_fallback():
    book = SplitNoticeBook((notice(),))
    assert book.lookup("ORLY/USD", "other-class", "split2025", at=SEEN)["status"] == "unknown"
    assert (
        book.lookup("ORLY/USD", "CIK0000898173-common", "split2026", at=SEEN)["status"] == "unknown"
    )


@pytest.mark.parametrize(
    "other",
    [
        {},
        {"revision_id": "r2"},
        {
            "revision_id": "r2",
            "security_id": "other-class",
            "published_at": PUB + timedelta(days=1),
            "observed_at": SEEN + timedelta(days=1),
        },
    ],
)
def test_duplicate_revision_publication_and_implicit_remapping_rejected(other):
    with pytest.raises(EquityAdmissionError):
        SplitNoticeBook((notice(), notice(**other)))


def test_actual_native_callback_clock_future_perturbation_truncation_and_copies():
    p = provider()
    with pytest.raises(EquityAdmissionError):
        p.get_split_notice("ORLY/USD", "CIK0000898173-common", "split2025")
    future = notice(
        revision_id="r2",
        published_at=PUB + timedelta(days=1),
        observed_at=SEEN + timedelta(days=1),
        new_shares=999,
    )
    variants = [
        p,
        provider((notice(), future)),
        provider((notice(), replace(future, new_shares=888))),
    ]
    for v in variants:
        v._set_dataframe_max_date(SEEN)
    values = [v.get_split_notice("ORLY/USD", "CIK0000898173-common", "split2025") for v in variants]
    assert values[0] == values[1] == values[2]
    values[0]["new_shares"] = -1
    assert p.get_split_notice("ORLY/USD", "CIK0000898173-common", "split2025")["new_shares"] == 15
    variants[1]._set_dataframe_max_date(SEEN + timedelta(days=1))
    assert (
        variants[1].get_split_notice("ORLY/USD", "CIK0000898173-common", "split2025")["new_shares"]
        == 999
    )


def test_hong_kong_exact_counter_and_calendar_binding():
    n = notice(pair="00700/HKD", calendar="XHKG", security_id="HK-class")
    p = provider((n,), "00700/HKD", "XHKG")
    p._set_dataframe_max_date(SEEN)
    assert p.get_split_notice("00700/HKD", "HK-class", "split2025")["new_shares"] == 15
    with pytest.raises(EquityAdmissionError):
        provider((n,))


def test_frozen_notices_and_book_reject_mutation():
    with pytest.raises(FrozenInstanceError):
        notice().new_shares = 999
    with pytest.raises(EquityAdmissionError):
        SplitNoticeBook([notice()])


def test_incomplete_historical_account_rejects_before_native_constructor(monkeypatch):
    from freqtrade.optimize.backtesting import Backtesting

    calls = []
    monkeypatch.setattr(Backtesting, "__init__", lambda *args, **kwargs: calls.append(True))
    spec = importlib.util.spec_from_file_location(
        "split_account_boundary", ROOT / "freqtrade/optimize/equity_backtesting.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(EquityAdmissionError, match="No complete equity account"):
        module.EquityBacktesting({})
    assert calls == []
