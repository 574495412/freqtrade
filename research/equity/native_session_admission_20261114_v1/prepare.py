"""Prepare raw equity rows/session calendars only; no scores or returns."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import exchange_calendars as xc
import pandas as pd
import yaml

HERE = Path(__file__).resolve().parent
VIBE = Path("/Users/chrise/GitHub/Vibe-Trading")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def main():
    if (HERE / "plan.json").exists():
        raise FileExistsError("Frozen input plan already exists")
    out = HERE / "inputs"
    out.mkdir(exist_ok=False)
    start, end = "2025-01-01", "2026-08-31"
    calendars = {}
    for name in ["XNYS", "XHKG"]:
        owner = xc.get_calendar(name, start="2024-01-01", end="2027-12-31")
        sessions = owner.sessions_in_range(start, end)
        calendar_path = out / (name + ".json")
        save(calendar_path, sessions.strftime("%Y-%m-%d").tolist())
        schedule = owner.schedule.loc[sessions]
        schedule.to_csv(out / (name + "_schedule.csv"))
        calendars[name] = dict(
            path=str(calendar_path),
            sha256=sha(calendar_path),
            schedule_path=str(out / (name + "_schedule.csv")),
            schedule_sha256=sha(out / (name + "_schedule.csv")),
            count=len(sessions),
        )
    us_bridge = (
        VIBE
        / "agent/research/us_edge_liquidity_native_20261112_v1/runtime/inputs_recent/data_bridge.yaml"
    )
    us = yaml.safe_load(us_bridge.read_text())["sources"]
    hk_receipt = (
        VIBE
        / "agent/research/crypto_equity_execution_interactions_20261110_v26/runtime/accounts_v1/runs/autopilot_048f74fe80be/research_provenance.json"
    )
    hk = json.loads(hk_receipt.read_text())["input_hashes"]
    records = []
    for calendar, entries in [
        ("XNYS", [(e["symbol"], Path(e["path"]), None) for e in us]),
        ("XHKG", [(Path(p).stem, Path(p), h) for p, h in hk.items()]),
    ]:
        for symbol, path, prior_hash in entries:
            digest = sha(path)
            if prior_hash is not None:
                assert digest == prior_hash, path
            frame = pd.read_csv(
                path,
                float_precision="round_trip",
                usecols=["date", "open", "high", "low", "close", "volume"],
            )
            frame["date"] = pd.to_datetime(frame.date, utc=True)
            frame = frame.loc[
                (frame.date >= pd.Timestamp(start, tz="UTC"))
                & (frame.date <= pd.Timestamp(end, tz="UTC"))
            ].copy()
            staged = out / (symbol + ".parquet")
            frame.to_parquet(staged, index=False)
            records.append(
                dict(
                    symbol=symbol,
                    calendar=calendar,
                    pair=symbol.rsplit(".", 1)[0] + ("/USD" if calendar == "XNYS" else "/HKD"),
                    source=str(path),
                    source_sha256=digest,
                    prior_receipt_verified=prior_hash is not None,
                    staged=str(staged),
                    staged_sha256=sha(staged),
                    rows=len(frame),
                )
            )
    freq = HERE.parents[2]
    native = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")
    manifest = native.parent / "FREQTRADE_SOURCE.json"
    extension = freq / "freqtrade/data/history/equity.py"
    save(
        HERE / "plan.json",
        dict(
            frozen_at_utc=datetime.now(UTC).isoformat(),
            scope="history integration; no alpha or account returns",
            start=start,
            end=end,
            native_source=str(native),
            native_manifest=str(manifest),
            native_manifest_sha256=sha(manifest),
            native_commit=json.loads(manifest.read_text())["commit"],
            local_extension=str(extension),
            local_extension_sha256=sha(extension),
            calendar_package="exchange_calendars",
            calendar_version=xc.__version__,
            calendar_status="local calendar package, not independent exchange certification",
            calendars=calendars,
            records=records,
            parent_sources={str(us_bridge): sha(us_bridge), str(hk_receipt): sha(hk_receipt)},
            rules=dict(
                no_missing_session_fill=True,
                no_failed_symbol_drop=True,
                unchanged_crypto_default=True,
                no_signal_or_return_computation=True,
                full_equity_account_implemented=False,
            ),
            preparation_sha256=sha(__file__),
        ),
    )
    print(
        "FROZEN", len(records), "inputs", {k: v["count"] for k, v in calendars.items()}, flush=True
    )


if __name__ == "__main__":
    main()
