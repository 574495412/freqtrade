"""Freeze source, universe, clocks and gates before computing exposures/labels."""

import json
from datetime import UTC, datetime
from pathlib import Path

import exchange_calendars as xc
import pandas as pd
import requests

from bootstrap import HERE, ROOT, sha


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def main():
    if (HERE / "protocol.json").exists():
        raise FileExistsError("No overwrite of the frozen protocol")
    parent = ROOT / "research/equity/native_session_admission_20261114_v1"
    plan = json.loads((parent / "plan.json").read_text())
    save(
        HERE / "protocol.json",
        dict(
            frozen_utc=datetime.now(UTC).isoformat(),
            market_scope="HK/US equity",
            parent_plan=str(parent / "plan.json"),
            parent_plan_sha256=sha(parent / "plan.json"),
            account_window=["2025-01-01", "2026-08-31"],
            warmup_start="2024-01-01",
            universe="Unchanged parent 503 US + 11 HK inputs; rejected names retained",
            source_definition="RR = 1 - calendar days since latest 52-week maximum closing price / 364",
            local_adaptation="Provider nominal quote closes, not certified CRSP split/dividend-adjusted prices; (t-364 days,t] window, latest exact tied maximum",
            source_reproduction=False,
            high52w_already_present=True,
            predictive_clock="Last session close before each calendar-month first session open; next month first-session OPEN/OPEN price-only label",
            incomplete_terminal_label="Exclude August 2026 decision: next monthly open is outside frozen window",
            factor_gates=dict(
                ic_mean_gt=0.03,
                ir_gt=0.5,
                positive_ratio_gt=0.55,
                minimum_full_observations=24,
                duplicate_mean_abs_pearson_gt=0.99,
            ),
            compare=["academic_high52w", "quote_momentum_252_21", "quote_reversal_5"],
            no_sign_window_universe_or_weight_rescue=True,
            known_limits=[
                "Fewer than 24 monthly observations in this window",
                "Quote-only corporate-action/return-label ambiguity",
                "Incomplete source universe and historical availability certification",
                "Native stock financial account unfinished",
            ],
            native_funded_accounts=0,
            source_strategy_reproduced=False,
            production_enabled=False,
            live_orders_authorized=False,
        ),
    )
    out = HERE / "inputs"
    out.mkdir()
    schedules = {}
    for name in ["XNYS", "XHKG"]:
        calendar = xc.get_calendar(name, start="2023-01-01", end="2027-12-31")
        sessions = calendar.sessions_in_range("2024-01-01", "2026-08-31")
        path = out / (name + "_schedule.csv")
        calendar.schedule.loc[sessions].to_csv(path)
        schedules[name] = dict(path=str(path), sha256=sha(path), count=len(sessions))
    records = []
    for rec in plan["records"]:
        assert sha(rec["source"]) == rec["source_sha256"]
        frame = pd.read_csv(
            rec["source"],
            float_precision="round_trip",
            usecols=["date", "open", "high", "low", "close", "volume"],
        )
        frame["date"] = pd.to_datetime(frame.date, utc=True)
        frame = frame.loc[frame.date.between("2024-01-01", "2026-08-31")].copy()
        staged = out / (rec["symbol"] + ".parquet")
        frame.to_parquet(staged, index=False)
        records.append(dict(rec, staged=str(staged), staged_sha256=sha(staged), rows=len(frame)))
    sources = HERE / "sources"
    sources.mkdir()
    downloaded = []
    for name, url in [
        ("recency_pead.pdf", "https://www.aobf.org/wp-content/uploads/2020/06/3.pdf"),
        ("george_hwang.pdf", "https://www.bauer.uh.edu/tgeorge/papers/gh4-paper.pdf"),
        ("qc_issue_120.json", "https://api.github.com/repos/QuantConnect/Tutorials/issues/120"),
    ]:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        path = sources / name
        path.write_bytes(response.content)
        downloaded.append(
            dict(url=url, path=str(path), sha256=sha(path), bytes=path.stat().st_size)
        )
    save(
        HERE / "inputs.json",
        dict(
            protocol_sha256=sha(HERE / "protocol.json"),
            schedules=schedules,
            records=records,
            calendar_package="exchange_calendars",
            calendar_version=xc.__version__,
            calendar_exchange_certified=False,
            sources=downloaded,
            social_parent_intake=str(parent / "source_intake.json"),
            social_parent_intake_sha256=sha(parent / "source_intake.json"),
            social_status="Discovery leads only; no new video watching or social-account return verification claimed",
        ),
    )
    print("FROZEN", len(records), "inputs; no factors/returns computed", flush=True)


if __name__ == "__main__":
    main()
