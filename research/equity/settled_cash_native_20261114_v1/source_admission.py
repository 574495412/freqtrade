"""Audit existing admitted filing history; do not fabricate an R&D predictor."""

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent
VIBE = Path("/Users/chrise/GitHub/Vibe-Trading")
PARENT = HERE.parent / "native_session_admission_20261114_v1/plan.json"
MEMBERS = VIBE / "agent/research/broad_factor_inputs_20261105_v1/protocol.json"
COVERAGE = VIBE / "agent/research/broad_universe_source_audit_20261105_v1/financial_coverage"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    members = json.loads(MEMBERS.read_text())["issuer_members"]
    by_symbol = {symbol + ".US": cik for cik, symbols in members.items() for symbol in symbols}
    records, dependencies, counts = [], {}, Counter()
    for source in json.loads(PARENT.read_text())["records"]:
        symbol = source["symbol"]
        cik = by_symbol.get(symbol)
        path = COVERAGE / (str(cik) + ".json")
        row = dict(
            symbol=symbol,
            market=source["calendar"],
            cik=cik,
            factor_input_admitted=False,
            score=None,
        )
        if source["calendar"] != "XNYS":
            row["reason"] = "No admitted comparable HK financial/disclosure panel"
        elif cik is None or not path.exists():
            row["reason"] = "No exact existing issuer/source binding"
        else:
            payload = json.loads(path.read_text())
            dependencies[str(path)] = sha(path)
            hist = payload["annual_records"]
            before = [v for v in hist.values() if v.get("available_at", "9999") < "2025-01-01"]
            row.update(
                cached_annual_records=len(hist),
                records_available_before_window=len(before),
                unique_fiscal_ends_before_window=len({v["period_end"] for v in before}),
                available_clocks=sorted(v.get("available_at") for v in hist.values()),
            )
            # The existing disclosure skeleton contains assets/cash/profit inputs, not a
            # fiscal-year R&D/sales regression panel. Raw facts may have older
            # disclosures; this check does not assert that those data do not exist.
            row["reason"] = (
                "No native-admitted R&D/sales fiscal-year history or source Compustat panel"
            )
            row["source_field_contract_admitted"] = False
        counts[row["reason"]] += 1
        records.append(row)
    result = dict(
        audited_utc=datetime.now(UTC).isoformat(),
        source="OpenSourceAP RDAbility",
        source_commit="9f053398c4995651d822e90d81526d0644c6f3e5",
        source_formula="Mean of five intercept OLS slopes of log annual sales growth on "
        "lag1..5 log(1+R&D/sales), rolling8/min6 pairs, frequency filter and "
        "contemporaneous high R&D-intensity tercile",
        minimum_history_note="Even one lag1 slope needs at least six valid growth/lag pairs; "
        "all five slopes need more history. Row-mean need not have all five.",
        source_python_caveat="shift(n) and rolling8 count rows; "
        "legacy Stata uses fiscal-year lags/windows. "
        "Fiscal gaps require explicit parity checks before implementation.",
        input_status="NOT_ADMITTED",
        economic_hypothesis_rejected=False,
        raw_companyfacts_scanned=False,
        raw_values_imputed=False,
        factor_registry_definitions_added=0,
        factor_analysis_calls=0,
        funded_freqtrade_stock_accounts=0,
        independent_oos=False,
        original_universe_preserved=len(records),
        counts=dict(counts),
        records=records,
        sources={str(PARENT): sha(PARENT), str(MEMBERS): sha(MEMBERS), **dependencies},
        next_integration="Native as-of fiscal-year R&D/sales input loader with accession clocks, "
        "gap checks and prefix invariance; then source math, Registry/FA and dedup. "
        "Full stock account remains separately blocked.",
    )
    (HERE / "rdability_input_admission.json").write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {k: result[k] for k in ["input_status", "original_universe_preserved", "counts"]}
        )
    )


if __name__ == "__main__":
    main()
