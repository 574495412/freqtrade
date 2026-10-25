"""Classify no-observed-trade carry prices without altering either official candle source."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, read_archive, save, sha
from research.crypto.prepare_nfi import complete


def audit(source, output, accounts):  # noqa: C901 - scoped cross-grain source reconciliation
    if output.exists():
        raise ValueError("Preserve previous quality audit")
    plan, records = read(source / "plan.json"), read(source / "archive_sources.json")
    parent = read(Path(plan["base_dataset"]) / "receipts.json")
    summary = read(accounts / "summary.json")
    if len(records) != len(plan["symbols"]) * 6:
        raise ValueError("Actual source-list cardinality differs from completed archives")
    report, mismatches, zero_minutes = [], [], {}
    for symbol in plan["symbols"]:
        selected = sorted([r for r in records if r["symbol"] == symbol], key=lambda r: r["period"])
        for row in selected:
            if sha(Path(row["path"])) != row["sha256"]:
                raise ValueError("Raw archive checksum changed")
        frame = complete(
            pd.concat([read_archive(Path(r["path"])) for r in selected]),
            plan["start"],
            plan["end"],
            "min",
        ).set_index("date")
        name = f"candles/futures/{symbol.removesuffix('USDT')}_USDT_USDT-5m-futures.feather"
        path = Path(plan["base_dataset"]) / name
        if sha(path) != parent[name]:
            raise ValueError("Frozen coarse comparator changed")
        native = (
            pd.read_feather(path).set_index("date").reindex(frame.resample("5min").size().index)
        )
        if native.isna().any().any():
            raise ValueError("Missing coarse source comparison clock")
        aggregation = {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
        folded = frame.resample("5min").agg(aggregation)
        # Comparison semantics only: an empty minute is retained in raw data and has
        # no observed transaction price. Nothing is filled or exported for execution.
        positive = (
            frame.loc[frame.volume > 0].resample("5min").agg(aggregation).reindex(native.index)
        )
        raw_bad, positive_bad = (
            pd.Series(False, index=native.index),
            pd.Series(False, index=native.index),
        )
        maxima = {}
        for field in ["open", "high", "low", "close"]:
            raw_bad |= ~np.isclose(native[field], folded[field], rtol=1e-12, atol=1e-12)
            positive_bad |= native.volume.gt(0) & ~np.isclose(
                native[field], positive[field], rtol=1e-12, atol=1e-12
            )
            maxima[field] = float((native[field] - folded[field]).abs().max())
        volume_bad = ~np.isclose(
            native.volume,
            folded.volume,
            atol=plan["volume_absolute_tolerance"],
            rtol=plan["volume_relative_tolerance"],
        )
        zero_minutes[symbol] = frame.index[frame.volume.eq(0)]
        for clock in native.index[raw_bad | positive_bad | volume_bad]:
            mismatches.append(
                {
                    "symbol": symbol,
                    "date": clock.isoformat(),
                    "raw_ohlc_differs": bool(raw_bad.loc[clock]),
                    "positive_trade_ohlc_differs": bool(positive_bad.loc[clock]),
                    "volume_differs": bool(volume_bad[native.index.get_loc(clock)]),
                    "native": native.loc[clock].to_dict(),
                    "all_minutes_folded": folded.loc[clock].to_dict(),
                }
            )
        row = {
            "symbol": symbol,
            "minute_rows": len(frame),
            "zero_volume_minutes": len(zero_minutes[symbol]),
            "coarse_rows": len(native),
            "coarse_zero_volume_rows": int(native.volume.eq(0).sum()),
            "raw_ohlc_mismatches": int(raw_bad.sum()),
            "positive_trade_ohlc_mismatches": int(positive_bad.sum()),
            "volume_mismatches": int(np.sum(volume_bad)),
            "maximum_raw_price_errors": maxima,
        }
        report.append(row)
        print(row, flush=True)
    exposures = []
    for account in summary["accounts"]:
        contacts = []
        for entry in account["campaign_audit"]["lease_entry_receipts"]:
            symbol = entry["pair"].split("/")[0] + "USDT"
            begin = pd.to_datetime(entry["open_timestamp"], unit="ms", utc=True)
            end = pd.to_datetime(entry["close_timestamp"], unit="ms", utc=True)
            zeros = zero_minutes[symbol]
            count = int(((zeros >= begin) & (zeros <= end)).sum())
            if count:
                contacts.append(
                    {
                        "pair": entry["pair"],
                        "open_timestamp": entry["open_timestamp"],
                        "close_timestamp": entry["close_timestamp"],
                        "zero_volume_minutes_during_position": count,
                        "entry_minute_has_no_observed_trade": bool(begin in zeros),
                        "exit_minute_has_no_observed_trade": bool(end in zeros),
                    }
                )
        exposures.append(
            {
                "candidate": account["scorecard"]["candidate"],
                "cost_bps": account["scorecard"]["extra_cost_bps_per_side"],
                "positions": contacts,
            }
        )
    result = {
        "audited_utc": datetime.now(UTC).isoformat(),
        "source_plan_sha256": sha(source / "plan.json"),
        "audit_source_sha256": sha(Path(__file__)),
        "coverage": report,
        "mismatches": mismatches,
        "actual_symbols": len(plan["symbols"]),
        "actual_archives": len(records),
        "actual_minute_rows": sum(x["minute_rows"] for x in report),
        "metadata_error_in_predecessor": {
            "declared_archives": plan["archives"],
            "correct_archives_from_39symbol_list": len(records),
            "declared_minute_rows": plan["expected_minute_rows"],
        },
        "zero_observation_exposures_in_coarse_accounts": exposures,
        "raw_values_changed": False,
        "normalized_detail_exported": False,
        "source_acceptable_for_unmasked_native_detail": False,
        "scope": "Zero volume means no observed trades,not proven lack of quotes or market closure."
        " Positive-minute aggregation is a diagnostic;raw candles are retained. Never use carry"
        " prices as proof of executable fills. Coarse backtests remain retrospective5m proxies.",
        "orders_enabled": False,
        "production_eligible": False,
    }
    save(output, result)
    print(
        {
            "symbols_checked": len(report),
            "raw_mismatch_bars": sum(x["raw_ohlc_mismatches"] for x in report),
            "unexplained_positive_price_mismatch_bars": sum(
                x["positive_trade_ohlc_mismatches"] for x in report
            ),
            "source_accepted_for_unmasked_detail": False,
        }
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--accounts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.source, args.output, args.accounts)
