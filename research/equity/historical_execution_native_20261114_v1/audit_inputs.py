"""Conditional HK quote-grid diagnostics using the actual native history API.

No security master, lots, fees, account returns, or hypothetical fills are
invented. 2800 is retained as an unsupported ETP; US inputs remain unprobed.
"""

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from bootstrap import boot

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PARENT = HERE.parent / "native_session_admission_20261114_v1"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def main():
    manifest = boot()
    import pandas as pd
    from freqtrade.data.equity_execution import hkex_equity_spreads
    from freqtrade.data.history.equity import (
        EquityAdmissionError,
        EquitySessionContract,
        load_equity_pair_history,
    )

    parent = json.loads((PARENT / "plan.json").read_text())
    protocol = dict(
        frozen_at_utc=datetime.now(UTC).isoformat(),
        scope="HK/US stocks only; execution component and conditional input diagnostics",
        target_window=[parent["start"], parent["end"]],
        universe_count=len(parent["records"]),
        parent_plan=str(PARENT / "plan.json"),
        parent_plan_sha256=sha(PARENT / "plan.json"),
        native_commit=manifest["commit"],
        native_source_files_verified=len(manifest["files"]),
        us="503 retained; no historical instrument precision contract supplied; NOT_PROBED",
        hk="10 ordinary-equity conditional quote-grid probes; 2800 ETP retained UNKNOWN",
        exact_price_policy="Never silently round prices; count binary-near-grid separately",
        board_lot_history="UNKNOWN for real securities; synthetic fixture lots are not history",
        financial_backtests=0,
        factor_analysis_calls=0,
        new_alpha_definitions=0,
        return_metrics=None,
        historical_first_seen_certified=False,
        source_rule_scope="HKEX ordinary Applicable Securities table; not ETP or auction rules",
        implementation={
            str(ROOT / p): sha(ROOT / p)
            for p in [
                "freqtrade/data/equity_execution.py",
                "freqtrade/optimize/equity_execution.py",
                "freqtrade/data/history/equity.py",
                "freqtrade/equity_wallets.py",
                "freqtrade/optimize/equity_backtesting.py",
            ]
        },
        source_receipts_sha256=sha(HERE / "source_receipts.json"),
    )
    assert not (HERE / "protocol.json").exists(), "Do not overwrite a frozen protocol"
    save(HERE / "protocol.json", protocol)
    grids = {
        m: tuple(json.loads(Path(r["path"]).read_text())) for m, r in parent["calendars"].items()
    }
    rows, cells, errors = [], 0, 0
    for record in parent["records"]:
        row = dict(symbol=record["symbol"], market=record["calendar"])
        if record["calendar"] == "XNYS":
            row["status"] = "NOT_PROBED_NO_HISTORICAL_INSTRUMENT_CONTRACT"
            rows.append(row)
            continue
        assert sha(record["staged"]) == record["staged_sha256"]
        assert sha(record["source"]) == record["source_sha256"]
        if record["symbol"] == "2800.HK":
            row["status"] = "UNKNOWN_ETP_TABLE_UNSUPPORTED"
            rows.append(row)
            continue
        declared = EquitySessionContract(record["pair"], "XHKG", grids["XHKG"])
        try:
            frame = load_equity_pair_history(PARENT / "native_history", declared)
        except EquityAdmissionError as exc:
            row.update(status="HISTORY_REJECTED", reason=str(exc))
            rows.append(row)
            continue
        pd.testing.assert_frame_equal(
            frame, pd.read_parquet(record["staged"]), check_exact=True, check_dtype=False
        )
        bad, near, examples = Counter(), Counter(), []
        for bar in frame.itertuples(index=False):
            table = hkex_equity_spreads(bar.date.date())
            for column in ("open", "high", "low", "close"):
                cells += 1
                price = getattr(bar, column)
                try:
                    table.require_price(price)
                except EquityAdmissionError as exc:
                    errors += 1
                    bad[column] += 1
                    value = Decimal(str(price))
                    try:
                        tick = table.tick_for(price)
                        remainder = value % tick
                        if min(remainder, tick - remainder) <= Decimal("1e-9"):
                            near[column] += 1
                    except EquityAdmissionError:
                        tick = None
                    if len(examples) < 6:
                        examples.append(
                            dict(
                                date=bar.date.isoformat(),
                                column=column,
                                price=str(price),
                                tick=str(tick),
                                reason=str(exc),
                            )
                        )
        row.update(
            status="CONDITIONAL_QUOTE_GRID_DIAGNOSTIC",
            rows=len(frame),
            checked_ohlc_cells=len(frame) * 4,
            off_grid_cells=dict(bad),
            binary_near_grid_rejected_cells=dict(near),
            first_examples=examples,
            original_source_sha256=record["source_sha256"],
            staged_sha256=record["staged_sha256"],
            security_master_certified=False,
            board_lot_history_certified=False,
            raw_unadjusted_units_certified=False,
        )
        rows.append(row)
    result = dict(
        protocol_sha256=sha(HERE / "protocol.json"),
        status_counts=dict(Counter(r["status"] for r in rows)),
        checked_ohlc_cells=cells,
        exact_off_grid_cells=errors,
        records=rows,
        financial_accounts=0,
        return_metrics=None,
        full_original_universe_admitted=False,
        caveat="Conditional quote checks do not establish historical classification, "
        "legal execution, raw units, first publication, or a complete account.",
    )
    save(HERE / "input_precision_audit.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "records"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
