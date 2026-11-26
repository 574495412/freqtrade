"""Audit pinned source functions/artifacts; never run its account simulator.

Synthetic fixtures test data/decision contracts only. Published returns remain
third-party gross historical claims, not native Freqtrade account evidence.
"""

import ast
import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent


def require(condition):
    if not condition:
        raise AssertionError("Source audit contract failed")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    (HERE / name).write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False, default=str) + "\n"
    )


def functions(path, names):
    """Compile exact selected pure definitions, excluding module imports/main."""
    tree = ast.parse(path.read_text())
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    require({n.name for n in selected} == set(names))
    namespace = {"pd": pd, "np": np}
    # Hash-checked pure source definitions only; excluded imports/account loop.
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)  # noqa: S102
    return namespace


def main():
    manifest = json.loads((HERE / "source_manifest.json").read_text())
    for record in manifest["files"]:
        require(sha(HERE / "source" / record["path"]) == record["sha256"])
    for record in json.loads((HERE / "provider/receipts.json").read_text()):
        require(sha(Path(record["path"])) == record["sha256"])
    for record in json.loads((HERE / "provider/early_price_receipts.json").read_text()):
        archive = HERE / "provider" / f"{record['symbol']}-2020-01-01.zip"
        checksum = archive.with_suffix(".CHECKSUM").read_text().split()[0]
        require(sha(archive) == record["sha256"] == checksum)
    source = HERE / "source"
    signal = functions(
        source / "strategy_selector.py",
        [
            "ema",
            "rsi",
            "atr",
            "roc",
            "volume_ratio",
            "add_indicators",
            "entry_signal",
            "update_trail_stop",
        ],
    )
    merge = functions(source / "backtester.py", ["merge_fg_onto_bars"])["merge_fg_onto_bars"]

    # Actual source changes an earlier unknown day when ONLY future input changes.
    clocks = pd.date_range("2025-01-01", "2025-01-02", freq="h", tz="UTC")
    bars = pd.DataFrame({"close": 100.0}, index=clocks)
    future = pd.Series([20.0], index=[clocks[-1].date()])
    base = merge(bars, future)
    changed = merge(bars, future + 70)
    prefix = merge(bars.iloc[:-1], future.iloc[:0])
    require(base.fg.iloc[0] == 20 and changed.fg.iloc[0] == 90)
    require(prefix.fg.isna().all())
    early_changed = int((base.fg.iloc[:-1] != changed.fg.iloc[:-1]).sum())
    require(early_changed == 24)

    # Daily-date keys cannot enforce a within-day release clock. This fixture
    # demonstrates capability, NOT an assertion of actual historical CMC release.
    noon = pd.Timestamp("2025-01-01T12:00Z")
    same_day = merge(bars.iloc[:24], pd.Series([80], index=[noon.date()]))
    require(same_day.fg.iloc[:12].eq(80).all())

    # RSI > 50 must be finite. Source RSI intentionally replaces zero loss by
    # NaN; its <=50 rejection then lets NaN through. Verify on actual official
    # first-day candles and the source's published earliest entries.
    trades = pd.read_csv(source / "outputs/trades.csv")
    actual_early = []
    for sym in ["BTCUSDT", "ETHUSDT"]:
        with zipfile.ZipFile(HERE / "provider" / f"{sym}-2020-01-01.zip") as archive:
            records = list(csv.reader(io.TextIOWrapper(archive.open(archive.namelist()[0]))))
        raw = pd.DataFrame(records)
        frame = raw.iloc[:, 1:6].astype(float)
        frame.columns = ["open", "high", "low", "close", "volume"]
        frame.index = pd.to_datetime(raw.iloc[:, 0].astype("int64"), unit="ms", utc=True)
        calculated = signal["add_indicators"](frame)
        row = calculated.iloc[1]
        require(np.isnan(row.rsi14))
        require(signal["entry_signal"](row, True))
        recorded = trades.loc[
            (trades.symbol == sym) & (pd.to_datetime(trades.entry_time, utc=True) == frame.index[1])
        ]
        require(len(recorded) == 1 and float(recorded.iloc[0].entry_price) == row.close)
        actual_early.append(
            {
                "symbol": sym,
                "bar_open_utc": str(frame.index[1]),
                "bar_close_utc_exclusive": str(frame.index[1] + pd.Timedelta(hours=1)),
                "source_recorded_entry_price": float(recorded.iloc[0].entry_price),
                "official_same_bar_close": float(row.close),
                "rsi14": None,
                "source_entry_function_accepts": True,
                "finite_rsi_gt_50": False,
            }
        )

    # The multiplier is fixed from ENTRY regime; the current row does not
    # tighten it. Preserve this source-vs-description distinction for any port.
    position = {"trail_stop": 80.0, "tighten_trail": False}
    row = pd.Series({"close": 100.0, "atr14": 10.0, "fg": 80})
    normal = signal["update_trail_stop"](dict(position), row)["trail_stop"]
    tighter = signal["update_trail_stop"]({**position, "tighten_trail": True}, row)["trail_stop"]
    require(normal == 80 and tighter == 85)
    falling_close = signal["update_trail_stop"](
        dict(position), pd.Series({"close": 95, "atr14": 1})
    )["trail_stop"]
    require(falling_close == 93)  # max(previous_stop, CURRENT close - fixed_k * current_ATR)

    equity = pd.read_csv(source / "outputs/equity.csv")
    dates = pd.to_datetime(equity.timestamp, utc=True)
    require(dates.is_monotonic_increasing and not dates.duplicated().any())
    expected = pd.date_range(dates.iloc[0], dates.iloc[-1], freq="h")
    missing = expected.difference(dates)
    require(len(missing) == 32)
    comparison = pd.date_range("2025-01-01T00:00Z", "2026-08-31T23:00Z", freq="h")
    absent_comparison = comparison.difference(dates)
    require(len(absent_comparison) == 2951)
    # All 32 INTERNAL source-calendar gaps precede the requested comparison;
    # the comparison fails coverage because the publication ends in May.
    require(not len(missing.intersection(comparison)))
    values = equity.equity.to_numpy(float)
    require(np.isfinite(values).all() and (values > 0).all())
    peak = values[0]
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = max(drawdown, 1 - value / peak)
    gross_return = values[-1] / values[0] - 1
    pnl = ((trades.exit_price - trades.entry_price) * trades.qty).to_numpy(float)
    errors = np.abs(pnl - trades.pnl_usd.to_numpy(float))
    require(errors.max() <= 0.0000501)
    spec = json.loads((source / "outputs/spec.json").read_text())["backtest_results"]
    require(len(trades) == spec["n_trades"] == 871)
    require(abs(round(100 * gross_return, 2) - spec["total_return_pct"]) < 1e-9)
    require(abs(round(-100 * drawdown, 2) - spec["max_drawdown_pct"]) < 1e-9)
    require(abs(values[-1] - values[0] - trades.pnl_usd.sum()) < 0.001)
    exit_counts = trades.exit_reason.value_counts().to_dict()
    require(exit_counts == spec["exit_reasons"])
    save(
        "source_artifact_audit.json",
        {
            "scope": (
                "Third-party gross spot artifacts; "
                "not our native account or independent market replication"
            ),
            "trade_rows": len(trades),
            "equity_rows": len(equity),
            "expected_hourly_rows": len(expected),
            "missing_hours_utc": [str(t) for t in missing],
            "duplicate_hours": 0,
            "start": str(dates.iloc[0]),
            "end": str(dates.iloc[-1]),
            "source_gross_return": gross_return,
            "source_observed_clock_drawdown": drawdown,
            "max_trade_pnl_rounding_error_usdt": float(errors.max()),
            "aggregate_pnl_reconciliation_error_usdt": float(
                abs(values[-1] - values[0] - trades.pnl_usd.sum())
            ),
            "source_summary_arithmetic_matches": True,
            "full_hourly_calendar": False,
            "missing_clock_reason": "UNKNOWN; no underlying whole-history raw OHLCV in source tree",
            "requested_comparison": {
                "start": str(comparison[0]),
                "end": str(comparison[-1]),
                "expected_hours": len(comparison),
                "uncovered_hours_after_source_end": len(absent_comparison),
                "internal_missing_hours_in_overlap": 0,
            },
            "fees_slippage_taxes": "Not simulated in source; cannot label gross artifact net",
        },
    )
    save(
        "function_checks.json",
        {
            "method": "Pinned pure functions via AST; no source financial loop invoked",
            "future_backfill": {
                "earlier_unknown_hours_changed_by_future_value_only": early_changed,
                "earlier_full_values": [20, 90],
                "prefix_only_value": None,
                "causal_pass": False,
            },
            "same_day_release_fixture": {
                "hypothetical_release_utc": str(noon),
                "pre_release_hours_assigned": 12,
                "actual_provider_historical_release_proof": False,
            },
            "actual_invalid_rsi_entries": actual_early,
            "trailing_semantics": {
                "multiplier_fixed_at_entry": True,
                "normal_entry_stop_despite_current_fg_80": normal,
                "extreme_greed_entry_stop": tighter,
                "falling_price_tightening_example": falling_close,
                "formula": "max(previous_stop, current_close - entry_regime_k * current_ATR)",
            },
            "financial_accounts_run": 0,
        },
    )
    save(
        "status.json",
        {
            "status": "SOURCE_EVIDENCE_REJECTED_BEFORE_NATIVE_FINANCE",
            "source_commit": manifest["commit"],
            "source_hashes_checked": len(manifest["files"]),
            "native_accounts": 0,
            "native_returns": None,
            "native_drawdown": None,
            "accepted_new_alphas": 0,
            "accepted_combinations": 0,
            "sentiment_family_economically_rejected": False,
            "goal_achieved": False,
            "goal_status": "active",
            "orders_enabled": False,
            "services_changed": False,
            "reasons": [
                "Actual source pure-function future backfill failure",
                "Actual early published entries fail a finite RSI>50 condition",
                "Same-bar-close execution without modeled fees/slippage",
                "32 absent published hourly clocks",
                "Exact source CMC historical input not obtained; anonymous endpoint HTTP401",
                "Two provider indices have different input methodologies; no silent substitution",
            ],
        },
    )
    print(
        json.dumps(
            {
                "audit_completed": True,
                "source_files_checked": len(manifest["files"]),
                "future_backfilled_hours": early_changed,
                "actual_invalid_rsi_entries": len(actual_early),
                "source_missing_hours": len(missing),
                "native_accounts": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
