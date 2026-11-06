"""Registered comparisons of launch shorts with and without BTC exposure."""

import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.listing_btc_hedge_20261107 import btc, paired, verify
from research.crypto.listing_funding_20261107 import replicate as a06
from research.crypto.listing_funding_20261107.verify_regimes import original_inputs
from research.crypto.listing_reversal_20261107 import audit, catalog, coverage
from research.crypto.listing_reversal_20261107 import replay as base


HERE = Path(__file__).resolve().parent
OUT = btc.OUT
PLAN = HERE / "registration.json"
STUDY = OUT / "accounts"
WINDOWS = ("2024", "2025_2026", "reset_2026")
CASES = ("FullShort", "SameShort", "PairedBTC")
SOURCES = (
    Path(__file__),
    Path(paired.__file__),
    Path(verify.__file__),
    Path(btc.__file__),
    base.PLAN,
    Path(base.__file__),
    Path(audit.__file__),
    a06.PLAN,
    Path(a06.__file__),
    a06.HERE / "result.json",
    a06.OUT / "admission.json",
    btc.REGISTRATION,
    OUT / "btc_receipts.json",
    coverage.HERE / "coverage_result.json",
    coverage.HERE / "reset_2026_registration.json",
    catalog.OUT / "admission.json",
    catalog.OUT / "catalog.json",
)


@dataclass
class BTCTape:
    name: str
    launch: int
    close: object
    quote: object
    mark_close: object
    mark_high: object
    mark_low: object
    funding: dict

    index = base.Market.index


def register():
    if PLAN.exists() or STUDY.exists():
        raise FileExistsError("A07 already registered")
    save(
        PLAN,
        {
            "registered_utc": catalog.now(),
            "source_hashes": {str(p.relative_to(catalog.ROOT)): sha(p) for p in SOURCES},
            "windows": WINDOWS,
            "cases": CASES,
            "btc_notional_per_short": 0.5,
            "hypothesis": (
                "A 0.5-notional BTC long alongside each new-listing short may offset common "
                "crypto-market risk and improve funded net returns over the same short allocation."
            ),
            "execution": (
                "Freeze A05 short entry, horizon and risk contract. At 1x use total campaign "
                "budget for both legs, 2/3 short and 1/3 BTC long. SameShort removes the BTC leg "
                "with the same 2/3 per-slot and overall short allocation. FullShort is unmodified "
                "A05. Paired fills use the same next-bar close and per-side costs, subject to "
                "both volume caps. BTC quote capacity is shared by all overlapping positions; "
                "paired partial fills keep the inventory fractions equal. A stop on either leg "
                "requests both exits. Catastrophic barrier charges the affected isolated model "
                "collateral, with any surviving leg queued for the next bar. No netting credits. "
                "Both legs use actual settled funding at raw clocks while held, observed at the "
                "first five-minute boundary not earlier than the event. This is a bar model."
            ),
            "gate": (
                "At both costs across every listed window require positive net return greater "
                "than SameShort, <=40% observed drawdown lower than FullShort, no account halt or "
                "catastrophic collateral loss, and at least 30 closed campaigns in 12 weeks. "
                "The high-return hurdle is a 25% annualized stress return in at least two windows "
                "and beating FullShort in at least one window. All results are retrospective."
            ),
            "orders_enabled": False,
        },
    )


def btc_market():
    verified = read(OUT / "btc_receipts.json")
    if verified["registration_sha256"] != sha(btc.REGISTRATION):
        raise ValueError("BTC tape is not registered")
    tables = {}
    for key, item in verified["sources"].items():
        path = OUT / f"{key}.parquet"
        audit.digest(path, item["parquet"])
        raw = OUT / key / "receipts.json"
        audit.digest(raw, item["raw_index"])
        rows = read(raw)
        if key == "funding":
            for index, row in enumerate(rows):
                audit.digest(raw.parent / f"page-{index:02}.json", row["sha256"])
        else:
            for row in rows:
                if row["state"] == "VERIFIED":
                    source = raw.parent / row["file"]
                    audit.digest(source, row["sha256"])
                    audit.digest(source, source.with_suffix(".zip.CHECKSUM").read_text().split()[0])
        tables[key] = pd.read_parquet(path)
    trade, mark = (tables[name] for name in ("klines", "markPriceKlines"))
    expected = pd.date_range(btc.START, btc.END, freq="5min", inclusive="left")
    if not trade.index.equals(expected.as_unit("us")) or not trade.index.equals(mark.index):
        raise ValueError("BTC tape clock mismatch")
    settlements = defaultdict(list)
    for actual, row in tables["funding"].iterrows():
        key = base.bar_number(actual.ceil("5min"))
        settlements[key].append((actual.isoformat(), row.rate, row.mark))
    return BTCTape(
        btc.SYMBOL,
        base.bar_number(btc.START),
        trade.close.to_numpy(),
        trade.quote_volume.to_numpy(),
        mark.close.to_numpy(),
        mark.high.to_numpy(),
        mark.low.to_numpy(),
        settlements,
    )


def windows():
    parent, old_panel, old_market, _ = a06.all_inputs()
    early = read(a06.PLAN)
    dates = {k: early["windows"]["2024"][k] for k in ("common_start", "split", "common_end")}
    selection = [row["symbol"] for row in early["symbols"]["2024"]]
    selected = {s: old_market[s] for s in selection if s in old_market}
    yield "2024", {**parent, **dates}, old_panel, selected
    parent, panel, markets = original_inputs()
    yield "2025_2026", parent, panel, markets
    reset = read(coverage.HERE / "reset_2026_registration.json")
    contract = {**parent, **{k: reset[k] for k in ("common_start", "split", "common_end")}}
    start = base.bar_number(pd.Timestamp(contract["common_start"], tz="UTC"))
    yield "reset_2026", contract, panel, {s: m for s, m in markets.items() if m.launch >= start}


def replay_window(window, plan, panel, markets, btc_tape, ratio):
    audit.OUT = STUDY / window
    audit.OUT.mkdir()
    result = {}
    for case in CASES:
        contract = {**plan, "btc_notional_per_short": ratio}
        if case == "SameShort":
            for field in ("stake_fraction", "margin_fraction"):
                contract[field] /= 1 + ratio
        for cost in contract["cost_bps"]:
            name = f"{case}_{cost}bps"
            directory = audit.OUT / name
            directory.mkdir()
            if case == "PairedBTC":
                account, opportunities = paired.simulate(contract, markets, cost, btc_tape)
            else:
                account, opportunities = base.simulate(contract, markets, "AllLaunches", cost)
            nav, economics = base.summarize(contract, account, opportunities)
            nav.to_parquet(directory / "nav.parquet", index=False)
            save(directory / "events.json", account.events)
            save(directory / "closed.json", account.finished)
            if case == "PairedBTC":
                evidence = verify.audit(contract, directory, markets, btc_tape, cost, economics)
            else:
                evidence = audit.audit_account(contract, panel, name, economics)
            result[name] = {"economics": economics, "verification": evidence}
            print(
                window,
                name,
                economics["net_return"],
                economics["max_drawdown"],
                economics["completed"],
                flush=True,
            )
    return result


def verify_broad_controls(results):
    early = read(a06.HERE / "result.json")
    inherited = read(coverage.HERE / "coverage_result.json")
    for cost in (15, 40):
        name = f"AllLaunches_{cost}bps"
        reference = {
            "2024": early["accounts"][f"2024/{name}"]["economics"],
            "2025_2026": inherited["accounts"][f"continuous/{name}"]["summary"],
            "reset_2026": inherited["accounts"][f"reset_2026/{name}"]["summary"],
        }
        for label, expected in reference.items():
            if expected != results[label][f"FullShort_{cost}bps"]["economics"]:
                raise ValueError(f"Broad control failed reproduction: {label}/{cost}")


def run():
    plan = read(PLAN)
    for name, digest in plan["source_hashes"].items():
        audit.digest(catalog.ROOT / name, digest)
    STUDY.mkdir()
    tape = btc_market()
    results = {
        label: replay_window(label, contract, panel, market, tape, plan["btc_notional_per_short"])
        for label, contract, panel, market in windows()
    }
    verify_broad_controls(results)
    failed = {}
    annual_hurdle = higher_full = 0
    for label, records in results.items():
        for cost in (15, 40):
            candidate = records[f"PairedBTC_{cost}bps"]["economics"]
            reduced = records[f"SameShort_{cost}bps"]["economics"]
            full = records[f"FullShort_{cost}bps"]["economics"]
            checks = {
                "positive_increment": candidate["net_return"] > max(0, reduced["net_return"]),
                "drawdown": candidate["max_drawdown"] < full["max_drawdown"]
                and candidate["max_drawdown"] <= 0.40,
                "no_stop": not candidate["halted"] and not candidate["catastrophic_losses"],
                "sample": candidate["completed"] >= 30 and candidate["entry_weeks"] >= 12,
            }
            failed[f"{label}/{cost}bps"] = [k for k, passed in checks.items() if not passed]
            if cost == 40:
                annual_hurdle += candidate["annualized_net_return"] >= 0.25
                higher_full += candidate["net_return"] > full["net_return"]
    save(
        HERE / "result.json",
        {
            "registration_sha256": sha(PLAN),
            "accounts": results,
            "failed_gates": failed,
            "historical_screen_passed": not any(failed.values()),
            "high_return_screen_passed": annual_hurdle >= 2 and higher_full >= 1,
            "full_short_baseline_exact": True,
            "paper_eligible": False,
        },
    )


if __name__ == "__main__":
    {"register": register, "run": run}[sys.argv[1]]()
