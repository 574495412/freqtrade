"""Descriptive blacklist/version/identity diagnostics, never account returns."""

import io
import subprocess
from collections import Counter
from zipfile import ZipFile

import pandas as pd
import rapidjson

from freqtrade.plugins.pairlist.pairlist_helpers import expand_pairlist
from research.crypto.nfi_blacklist_rules_20261109.inputs import (
    BASE,
    HERE,
    OUT,
    PLAN,
    candidates,
    read,
    ref,
    save,
    structural_rules,
)


def parse(blob):
    return rapidjson.loads(blob, parse_mode=3)["exchange"]["pair_blacklist"]


def main():
    repo = "/Users/chrise/GitHub/NostalgiaForInfinity"
    pin = "1af55971d3a64703ec449078e09fdec257d0d3c6"
    path = "configs/blacklist-binance.json"
    pre = subprocess.run(
        [
            "git",
            "-C",
            repo,
            "log",
            pin,
            "-1",
            "--before=2026-01-01T00:00:00Z",
            "--format=%H",
            "--",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    blob = subprocess.run(
        ["git", "-C", repo, "show", pre + ":" + path], capture_output=True, check=True
    ).stdout
    (HERE / "blacklist-pre2026.json").write_bytes(blob)
    universe = rapidjson.loads((BASE / "author-universe.json").read_text(), parse_mode=3)[
        "exchange"
    ]["pair_whitelist"]
    old_rules = parse((BASE / "author-blacklist.json").read_text())
    old = set(expand_pairlist(old_rules, universe))
    earlier = set(expand_pairlist(parse(blob.decode()), universe))
    latest = set(
        expand_pairlist(parse((HERE / "remote-latest-blacklist.json").read_text()), universe)
    )
    structural = set(expand_pairlist(structural_rules(), universe))
    active = set(candidates())
    risk = read(OUT / "native_risk-schedule.json")["days"]
    rows = []
    for pair in sorted(old & active):
        restricted = [
            (d, r["risk_exclusions"].get(pair))
            for d, r in risk.items()
            if pair in r["risk_exclusions"]
        ]
        rows.append(
            {
                "pair": pair,
                "structural_blocked": pair in structural,
                "selected_days": sum(pair in r["pairs"] for r in risk.values()),
                "risk_excluded_days": len(restricted),
                "exclusion_reasons": dict(Counter(v for _, v in restricted)),
                "latest_day_selected": pair in risk["2026-11-09"]["pairs"],
            }
        )
    market = read(PLAN["original_full_market_snapshot"]["path"])["markets"]["LIT/USDT:USDT"]
    first = pd.read_feather(OUT / "candles/futures/LIT_USDT_USDT-5m-futures.feather").iloc[0]
    archive = OUT / "raw/LITUSDT/klines-2025-12.zip"
    with ZipFile(archive) as z:
        data = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
    times = pd.to_datetime(data.iloc[:, 0], unit="ms", utc=True)
    onboard = pd.to_datetime(int(market["info"]["onboardDate"]), unit="ms", utc=True)
    earlier_archive = data.loc[times < onboard]
    latest_meta = read(HERE / "remote-latest-commit.json")
    result = {
        "frozen_source_commit": pin,
        "frozen_source_date": "2026-10-22",
        "latest_author_blacklist_commit": latest_meta["sha"],
        "latest_author_commit_date": latest_meta["commit"]["committer"]["date"],
        "latest_author_source": ref(HERE / "remote-latest-blacklist.json"),
        "pre2026_git_snapshot": pre,
        "pre2026_blacklist": ref(HERE / "blacklist-pre2026.json"),
        "publication_clock_limitation": "Git commit clocks alone do not certify public event publication time; this audit is not an event feed.",
        "author_candidates": len(universe),
        "frozen_regex_rules": len(old_rules),
        "frozen_matched_candidates": len(old),
        "pre2026_matched_candidates": len(earlier),
        "latest_matched_candidates": len(latest),
        "new_since_pre2026": sorted(old - earlier),
        "latest_added_vs_frozen": sorted(latest - old),
        "latest_removed_vs_frozen": sorted(old - latest),
        "current_active_with_tiers": len(active),
        "frozen_blacklist_currently_active": len(old & active),
        "frozen_blacklist_already_not_currently_eligible": len(old - active),
        "structural_current_blocks": sorted(structural & active),
        "restored_named_candidates": sorted((old & active) - structural),
        "named_candidate_rule_diagnostics": rows,
        "LIT_identity_evidence": {
            "old_underlying": "Litentry",
            "old_contract_retirement_utc": "2025-01-31T09:00:00Z",
            "old_official_notice": "https://www.binance.com/en/support/announcement/detail/2a9feaa556f74dcdaa2192366f0e247c",
            "new_underlying": "Lighter Protocol",
            "new_contract_onboard_utc": str(onboard),
            "new_official_notice": "https://www.binance.com/en-AU/support/announcement/detail/6a33be00231c4539b3a4a625538e4d1e",
            "pre_onboard_archive_bars": len(earlier_archive),
            "pre_onboard_archive_unique_open": int(earlier_archive.iloc[:, 1].nunique()),
            "pre_onboard_archive_open_sample": float(earlier_archive.iloc[0, 1]),
            "accepted_first_native_5m_utc": str(first.date),
            "accepted_first_open": float(first.open),
            "listing_calendar_receipt": ref(OUT / "raw/LITUSDT/admitted.json"),
            "no_prelisting_quotes_manufactured": True,
        },
        "manual_event_history_status": "NOT_RECONSTRUCTED; price gates cannot predict hacks or certify project safety",
        "diagnostic_only_no_portfolio_simulation": True,
        "orders_enabled": False,
    }
    save(HERE / "source-audit.json", result)
    print(
        {
            k: result[k]
            for k in [
                "pre2026_matched_candidates",
                "frozen_matched_candidates",
                "latest_matched_candidates",
                "frozen_blacklist_currently_active",
                "structural_current_blocks",
            ]
        }
    )


if __name__ == "__main__":
    main()
