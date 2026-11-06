"""Retain the A06 filter and test additional dated launch cohorts."""

import sys
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.listing_funding_20261107 import replicate as a06
from research.crypto.listing_reversal_20261107 import audit, catalog, coverage, intake, replay


HERE = a06.HERE
REGISTRATION = HERE / "regime_registration.json"
OUT = a06.OUT.parent / "listing_funding_regimes_20261107_v001"
ORIGINAL = catalog.OUT
SOURCES = (
    Path(__file__),
    *a06.SOURCES,
    a06.PLAN,
    HERE / "result.json",
    ORIGINAL / "admission.json",
    coverage.HERE / "reset_2026_registration.json",
)
YEAR = {"common_start": "2023-01-01", "split": "2023-07-01", "common_end": "2024-01-01"}
LAST_2023 = "2023-12-27"


def register():
    if REGISTRATION.exists() or OUT.exists():
        raise FileExistsError("Regime supplement already registered")
    previous = read(a06.PLAN)
    metadata = read(a06.CATALOG)["symbols"]
    available, excluded = [], {}
    for row in metadata:
        day = row.get("first_archive_day")
        if not day or not YEAR["common_start"] <= day < LAST_2023:
            continue
        name = row["symbol"]
        invalid = (
            name.removesuffix("USDT") in intake.STABLE
            or row.get("contract_type") not in (None, "PERPETUAL")
            or not row.get("collector_since")
            or row["collector_since"][:10] != day
            or (row.get("onboard_utc") and row["onboard_utc"][:10] != day)
        )
        if invalid:
            excluded[name] = "A06 metadata admission failed"
        else:
            available.append(row)
    if not 0 < len(available) <= 200:
        raise ValueError("Funding request budget exceeded")
    save(
        REGISTRATION,
        {
            "registered_utc": catalog.now(),
            "sources": {str(path.relative_to(catalog.ROOT)): sha(path) for path in SOURCES},
            "symbols": {"2023": available},
            "metadata_exclusions_2023": excluded,
            "2023_window": YEAR,
            "2023_last_launch_exclusive": LAST_2023,
            "factor": previous["factor"],
            "policy": (
                "Add the disjoint 2023 year with no changes to the A06 gate or A05 execution. "
                "Also apply the fixed A06 gate to the already published 2025–26 A05 market "
                "cohort and the same 2026 cash reset. The original broad A05 economics were "
                "known, and all supplementation is retrospective. Preserve earlier results."
            ),
            "orders_enabled": False,
        },
    )


def original_inputs():
    parent = read(replay.PLAN)
    intake_receipt = read(ORIGINAL / "admission.json")
    recovered = read(coverage.HERE / "coverage_result.json")["recovered"]
    audit.OUT = replay.OUT = ORIGINAL
    panel = audit.audit_sources()
    markets = {m.name: m for m in replay.market_data(parent, intake_receipt)}
    replay.OUT = ORIGINAL / "coverage_recovery"
    for row in recovered:
        name = row["symbol"]
        source = replay.OUT / "data" / name
        audit.digest(source / "funding.json", row["funding_raw_sha256"])
        panel[name] = (
            *(pd.read_parquet(source / f"{kind}.parquet") for kind in ("trade", "mark", "funding")),
            replay.bar_number(row["launch_utc"]),
        )
    for market in replay.market_data(parent, {**intake_receipt, "rows": recovered}):
        markets[market.name] = market
    return parent, panel, markets


def run_accounts(plan, label, contract, panel, markets):
    audit.OUT = folder = OUT / label
    folder.mkdir()
    start = replay.bar_number(pd.Timestamp(contract["common_start"], tz="UTC"))
    pool = {s: m for s, m in markets.items() if m.launch >= start}
    choices = a06.signal_receipts(plan, {s: panel[s] for s in pool})
    signal_file = folder / "signals.json"
    save(signal_file, choices)
    accounts = {}
    for case, selected in (
        ("AllLaunches", pool),
        ("FundingScreen", {s: m for s, m in pool.items() if choices[s]["accepted"]}),
    ):
        for cost in contract["cost_bps"]:
            key = f"{case}_{cost}bps"
            path = folder / key
            path.mkdir()
            account, opportunities = replay.simulate(contract, selected, "AllLaunches", cost)
            curve, summary = replay.summarize(contract, account, opportunities)
            curve.to_parquet(path / "nav.parquet", index=False)
            save(path / "events.json", account.events)
            save(path / "closed.json", account.finished)
            if case == "FundingScreen":
                a06.review_entries(plan, account.events, panel)
            verification = audit.audit_account(contract, panel, key, summary)
            accounts[key] = {"economics": summary, "verification": verification}
            print(
                label,
                key,
                summary["net_return"],
                summary["max_drawdown"],
                summary["completed"],
                flush=True,
            )
    return {"accounts": accounts, "admitted": len(pool), "signals_sha256": sha(signal_file)}


def main():
    command = sys.argv[1]
    if command == "register":
        register()
        return
    plan = read(REGISTRATION)
    for name, digest in plan["sources"].items():
        audit.digest(catalog.ROOT / name, digest)
    a06.OUT, a06.PLAN = OUT, REGISTRATION
    if command == "acquire":
        a06.acquire(plan)
        return
    parent, older_panel, older_market, intake_receipt = a06.all_inputs()
    older = run_accounts(plan, "2023", {**parent, **plan["2023_window"]}, older_panel, older_market)
    parent, panel, markets = original_inputs()
    main_window = run_accounts(plan, "2025_2026", parent, panel, markets)
    old_reset = read(coverage.HERE / "reset_2026_registration.json")
    reset = {**parent, **{k: old_reset[k] for k in ("common_start", "split", "common_end")}}
    reset_window = run_accounts(plan, "reset_2026", reset, panel, markets)
    save(
        HERE / "regime_result.json",
        {
            "registration_sha256": sha(REGISTRATION),
            "2023_intake_sha256": sha(OUT / "admission.json"),
            "2023_unknown": intake_receipt["unknown"],
            "cohorts": {"2023": older, "2025_2026": main_window, "reset_2026": reset_window},
            "primary_decision_unchanged": True,
            "paper_eligible": False,
        },
    )


if __name__ == "__main__":
    main()
