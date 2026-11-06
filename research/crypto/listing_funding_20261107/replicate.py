"""Replicate the listing short and screen first-day funding on disjoint cohorts."""

import math
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.listing_reversal_20261107 import audit, catalog, coverage, intake, replay


HERE = Path(__file__).resolve().parent
OUT = catalog.OUT.parent / "listing_funding_20261107_v001"
PLAN = HERE / "registration.json"
CATALOG = catalog.OUT / "catalog.json"
WINDOWS = {
    "2024": {
        "common_start": "2024-01-01",
        "split": "2024-07-01",
        "common_end": "2025-01-01",
        "last_launch_exclusive": "2024-12-27",
    },
    "late_2026": {
        "common_start": "2026-09-06",
        "split": "2026-10-01",
        "common_end": "2026-11-06",
        "last_launch_exclusive": "2026-11-01",
    },
}
SOURCES = (
    Path(__file__),
    CATALOG,
    replay.PLAN,
    coverage.PROTOCOL,
    coverage.HERE / "coverage_result.json",
    Path(replay.__file__),
    Path(intake.__file__),
    Path(coverage.__file__),
    Path(audit.__file__),
    Path(catalog.__file__),
    catalog.ROOT / "freqtrade/research/data.py",
)


def register():
    if OUT.exists() or PLAN.exists():
        raise FileExistsError("A06 already registered")
    rows = read(CATALOG)["symbols"]
    admitted, rejected = {}, {}
    for name, window in WINDOWS.items():
        admitted[name], rejected[name] = [], {}
        for row in rows:
            day = row.get("first_archive_day")
            if not day or not window["common_start"] <= day < window["last_launch_exclusive"]:
                continue
            symbol = row["symbol"]
            if symbol.removesuffix("USDT") in intake.STABLE:
                rejected[name][symbol] = "stablecoin base"
            elif row.get("contract_type") not in (None, "PERPETUAL"):
                rejected[name][symbol] = "non-crypto-perpetual contract type"
            elif not row.get("collector_since") or row["collector_since"][:10] != day:
                rejected[name][symbol] = "collector calendar disagreement"
            elif row.get("onboard_utc") and row["onboard_utc"][:10] != day:
                rejected[name][symbol] = "exchange onboard disagreement or symbol reuse"
            else:
                admitted[name].append(row)
    if not 0 < sum(map(len, admitted.values())) <= 350:
        raise ValueError("Funding request budget exceeded")
    save(
        PLAN,
        {
            "registered_utc": catalog.now(),
            "windows": WINDOWS,
            "symbols": admitted,
            "metadata_exclusions": rejected,
            "sources": {str(path.relative_to(catalog.ROOT)): sha(path) for path in SOURCES},
            "factor": {
                "minimum_visible_funding_observations": 2,
                "visibility_buffer_minutes": 5,
                "exclude_first_minutes": 5,
                "minimum_first_day_rate_sum": -0.001,
                "hypothesis": (
                    "At listing plus 24h, skipping coins whose already settled first-day funding "
                    "implies shorts paid more than 10 bps reduces adverse carry enough to improve "
                    "net returns versus shorting all eligible launches. Same size, entries, "
                    "slot ordering, fees, 72h exits and stop rules."
                ),
            },
            "parent": (
                "A05 and the coverage recovery are frozen. Both new launch cohorts and their "
                "execution calendars are disjoint from A05. Each window starts from cash. "
                "The market data are historical and the original A05 returns were known."
            ),
            "gate": (
                "At both costs require 95% input coverage in each window, positive net return "
                "and full-window excess over the unchanged broad short, no halt or catastrophic "
                "collateral loss, drawdown <=40%, 30 completed positions spanning 12 distinct "
                "entry weeks in 2024 and 12 positions over 6 weeks in late 2026. Record 25% "
                "stress CAGR independently for the earlier longer window. Unknowns remain visible."
            ),
            "orders_enabled": False,
        },
    )


def recover_all(rows, admitted):
    coverage.OUT = OUT
    coverage.STAGE = OUT / "repair"
    (coverage.STAGE / "data").mkdir(parents=True)
    fixed, unknown = [], {}
    for row in rows:
        if row["state"] == "ADMITTED":
            continue
        can_recover = row.get("launch_utc") and row["reason"].startswith(
            ("Invalid funding cash inputs", "Incomplete markPriceKlines")
        )
        if not can_recover:
            unknown[row["symbol"]] = row["reason"]
            continue
        try:
            fixed_row, tables = coverage.recover(row)
        except (ValueError, KeyError, OSError) as error:
            unknown[row["symbol"]] = str(error)
        else:
            fixed.append(fixed_row)
            admitted[row["symbol"]] = tables
    return fixed, unknown


def initial_panel(rows):
    panel = {}
    for row in rows:
        path = OUT / "data" / row["symbol"]
        for kind in ("trade", "mark", "funding"):
            audit.digest(path / f"{kind}.parquet", row[f"{kind}_sha256"])
        audit.digest(path / "funding.json", row["funding_response_sha256"])
        for item in row["archive_receipts"]:
            if item["state"] == "VERIFIED":
                archived = path / item["file"]
                audit.digest(archived, item["sha256"])
                audit.digest(archived, archived.with_suffix(".zip.CHECKSUM").read_text().split()[0])
        trade, mark, funding = (
            pd.read_parquet(path / f"{k}.parquet") for k in ("trade", "mark", "funding")
        )
        raw = read(path / "funding.json")
        if list(zip(funding.index.as_unit("ms").asi8, funding.rate, funding.mark, strict=True)) != [
            (r["fundingTime"], float(r["fundingRate"]), float(r["markPrice"])) for r in raw
        ]:
            raise ValueError(f"Funding changed during conversion: {row['symbol']}")
        panel[row["symbol"]] = (trade, mark, funding, replay.bar_number(row["launch_utc"]))
    return panel


def acquire(plan):
    intake.OUT = OUT
    (OUT / "data").mkdir(parents=True)
    jobs = [row for symbols in plan["symbols"].values() for row in symbols]
    raw = catalog.OUT / "catalog"
    for row in jobs:
        audit.digest(raw / "first" / f"{row['symbol']}.xml", row["receipt_sha256"])
    with ThreadPoolExecutor(max_workers=12) as pool:
        rows = list(pool.map(lambda row: intake.admit(row, 120), jobs))
    good = [row for row in rows if row["state"] == "ADMITTED"]
    panel = initial_panel(good)
    fixed, unknown = recover_all(rows, panel)
    receipt = {
        "registration_sha256": sha(PLAN),
        "initial": rows,
        "recovered": fixed,
        "unknown": unknown,
    }
    save(OUT / "admission.json", receipt)
    print("admission", len(rows), len(good), len(fixed), len(unknown), flush=True)


def all_inputs():
    intake_receipt = read(OUT / "admission.json")
    if intake_receipt["registration_sha256"] != sha(PLAN):
        raise ValueError("Intake receipt does not match frozen protocol")
    parent = read(replay.PLAN)
    initial = [r for r in intake_receipt["initial"] if r["state"] == "ADMITTED"]
    fixed = intake_receipt["recovered"]
    panel = initial_panel(initial)
    for row in fixed:
        source = OUT / "repair/data" / row["symbol"]
        for kind in ("trade", "mark", "funding"):
            audit.digest(source / f"{kind}.parquet", row[f"{kind}_sha256"])
        audit.digest(source / "funding.json", row["funding_raw_sha256"])
        if "mark_api_sha256" in row:
            audit.digest(source / "mark-api.json", row["mark_api_sha256"])
        panel[row["symbol"]] = (
            *(pd.read_parquet(source / f"{k}.parquet") for k in ("trade", "mark", "funding")),
            replay.bar_number(row["launch_utc"]),
        )
    markets = {}
    for root, rows in ((OUT, initial), (OUT / "repair", fixed)):
        replay.OUT = root
        markets.update(
            {
                m.name: m
                for m in replay.market_data(
                    parent,
                    {
                        "rows": rows,
                        "registration_sha256": parent["admission_registration_sha256"],
                    },
                )
            }
        )
    return parent, panel, markets, intake_receipt


def signal_receipts(plan, panel):
    rules = plan["factor"]
    choices = {}
    for symbol, (trade, _, funding, _) in panel.items():
        start = trade.index[0]
        decision = start + pd.Timedelta(hours=24)
        available = decision - pd.Timedelta(minutes=rules["visibility_buffer_minutes"])
        after = start + pd.Timedelta(minutes=rules["exclude_first_minutes"])
        visible = funding.loc[(funding.index > after) & (funding.index <= available), "rate"]
        total = math.fsum(visible)
        choices[symbol] = {
            "decision_utc": decision.isoformat(),
            "visibility_cutoff_utc": available.isoformat(),
            "funding_observations": len(visible),
            "funding_rate_sum": total,
            "accepted": (
                len(visible) >= rules["minimum_visible_funding_observations"]
                and total >= rules["minimum_first_day_rate_sum"]
            ),
        }
    return choices


def review_entries(plan, events, panel):
    threshold = plan["factor"]["minimum_first_day_rate_sum"]
    required = plan["factor"]["minimum_visible_funding_observations"]
    buffer = plan["factor"]["visibility_buffer_minutes"] * 60 * 1000
    initial = plan["factor"]["exclude_first_minutes"] * 60 * 1000
    for event in events:
        if event["kind"] != "entry":
            continue
        trade, _, funding, _ = panel[event["symbol"]]
        start = int(trade.index[0].timestamp() * 1000)
        decision = (event["clock"] - 1) * 300_000
        clocks = funding.index.as_unit("ms").asi8
        visible = [
            value
            for at, value in zip(clocks, funding.rate, strict=True)
            if start + initial < at <= decision - buffer
        ]
        if len(visible) < required or math.fsum(visible) < threshold:
            raise ValueError("Funding filter admitted an unexplained trade")


def run(plan):
    parent, panel, markets, intake_receipt = all_inputs()
    choices = signal_receipts(plan, panel)
    save(OUT / "funding_signal_receipts.json", choices)
    results, failures = {}, {}
    for window, dates in plan["windows"].items():
        audit.OUT = directory = OUT / window
        directory.mkdir()
        dates = {k: dates[k] for k in ("common_start", "split", "common_end")}
        contract = {**parent, **dates}
        names = [r["symbol"] for r in plan["symbols"][window]]
        pool = {name: markets[name] for name in names if name in markets}
        for name, selected in (
            ("AllLaunches", pool),
            ("FundingScreen", {s: m for s, m in pool.items() if choices[s]["accepted"]}),
        ):
            for cost in contract["cost_bps"]:
                key = f"{name}_{cost}bps"
                path = directory / key
                path.mkdir()
                account, opportunities = replay.simulate(contract, selected, "AllLaunches", cost)
                curve, result = replay.summarize(contract, account, opportunities)
                curve.to_parquet(path / "nav.parquet", index=False)
                save(path / "events.json", account.events)
                save(path / "closed.json", account.finished)
                if name == "FundingScreen":
                    review_entries(plan, account.events, panel)
                verification = audit.audit_account(contract, panel, key, result)
                results[f"{window}/{key}"] = {"economics": result, "verification": verification}
                print(
                    window,
                    key,
                    result["net_return"],
                    result["max_drawdown"],
                    result["completed"],
                    flush=True,
                )
        for cost in contract["cost_bps"]:
            candidate = results[f"{window}/FundingScreen_{cost}bps"]["economics"]
            broad = results[f"{window}/AllLaunches_{cost}bps"]["economics"]
            min_trades, min_weeks = (30, 12) if window == "2024" else (12, 6)
            checks = {
                "coverage": len(pool) / len(names) >= 0.95,
                "net_excess": candidate["net_return"] > max(0, broad["net_return"]),
                "sample": candidate["completed"] >= min_trades
                and candidate["entry_weeks"] >= min_weeks,
                "risk": not candidate["halted"]
                and not candidate["catastrophic_losses"]
                and candidate["max_drawdown"] <= 0.40,
            }
            failures[f"{window}/{cost}bps"] = [k for k, passed in checks.items() if not passed]
    save(
        HERE / "result.json",
        {
            "registration_sha256": sha(PLAN),
            "intake_sha256": sha(OUT / "admission.json"),
            "signal_sha256": sha(OUT / "funding_signal_receipts.json"),
            "unknown": intake_receipt["unknown"],
            "recovered_count": len(intake_receipt["recovered"]),
            "accounts": results,
            "failed_gates": failures,
            "historical_screen_passed": not any(failures.values()),
            "upside_2024_screen_passed": (
                results["2024/FundingScreen_40bps"]["economics"]["annualized_net_return"] >= 0.25
            ),
            "paper_eligible": False,
        },
    )


def main():
    command = sys.argv[1]
    if command == "register":
        register()
        return
    plan = read(PLAN)
    for name, checksum in plan["sources"].items():
        audit.digest(catalog.ROOT / name, checksum)
    {"acquire": acquire, "run": run}[command](plan)


if __name__ == "__main__":
    main()
