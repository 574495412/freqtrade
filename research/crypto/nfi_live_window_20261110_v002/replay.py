"""New immutable two-hour attempt using the existing native replay workflow."""

import argparse
import importlib
import json
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pandas as pd

from research.crypto.nfi_live_window_20261110 import prepare as intake


ROOT = intake.ROOT
HERE = Path(__file__).resolve().parent
OUT = ROOT / "user_data/crypto_quant/nfi_live_window_20261110_v002"
PREVIOUS = ROOT / "user_data/crypto_quant/nfi_live_window_20261110_v001"
START = pd.Timestamp("2026-11-10T07:50:00Z")
END = pd.Timestamp("2026-11-10T09:50:00Z")
save, sha = intake.save, intake.sha


def configure():
    for name in ("HERE", "OUT", "START", "END"):
        setattr(intake, name, globals()[name])
    native = importlib.import_module("research.crypto.nfi_live_window_20261110.run")
    for name in ("HERE", "OUT", "START", "END"):
        setattr(native, name, globals()[name])
    return native


def freeze():
    if OUT.exists():
        raise ValueError("Attempt already exists; preserve it")
    if pd.Timestamp.now(tz="UTC") < END:
        raise ValueError("Window is not closed")
    if shutil.disk_usage(ROOT).free < 512 * 1024**2:
        raise ValueError("Insufficient free disk for isolated native attempt")
    manifest = PREVIOUS / "input-admission.json"
    previous = json.loads(manifest.read_text())
    if not previous["complete"]:
        raise ValueError("Previous input attempt incomplete")
    for record in previous["records"]:
        for path, digest in record["files"].items():
            if sha(PREVIOUS / path) != digest:
                raise ValueError("Previous input changed")
    plan = intake.freeze()
    for market, account in plan["accounts"].items():
        account["historical_data"] = str(PREVIOUS / market / "candles")
        folder = ROOT / "user_data/deployment/mainnet-100-20261109" / market
        with closing(
            sqlite3.connect((folder / "trades.sqlite").as_uri() + "?mode=ro", uri=True)
        ) as db:
            trades = pd.read_sql_query("SELECT open_date,close_date FROM trades", db)
        if not trades.empty:
            opened = pd.to_datetime(trades.open_date, utc=True)
            closed = pd.to_datetime(trades.close_date, utc=True)
            if ((opened < START) & (closed.isna() | (closed >= START))).any():
                raise ValueError("Live position existed at window start; empty replay invalid")
        account["live_start_positions"] = 0
    sources = [
        Path(__file__),
        HERE / "WindowReplay.py",
        ROOT / "research/crypto/nfi_live_window_20261110/prepare.py",
        ROOT / "research/crypto/nfi_live_window_20261110/run.py",
    ]
    plan["orchestration_sources"] = {str(p.relative_to(ROOT)): sha(p) for p in sources}
    plan["previous_admission"] = {"path": str(manifest), "sha256": sha(manifest)}
    plan["scope"] = "New two-hour native replay; no parameter optimization or live mutation"
    save(OUT / "plan.json", plan)
    save(HERE / "protocol.json", plan)
    print(json.dumps({"frozen": True, "start": str(START), "end_exclusive": str(END)}))


def verify():
    plan = json.loads((OUT / "plan.json").read_text())
    for path, digest in plan["orchestration_sources"].items():
        if sha(ROOT / path) != digest:
            raise ValueError("Frozen workflow source changed")
    with closing(
        sqlite3.connect(
            (ROOT / "user_data/deployment/binance-request-guard.sqlite").as_uri() + "?mode=ro",
            uri=True,
        )
    ) as db:
        row = db.execute("SELECT cooldown,strikes FROM budget WHERE id=1").fetchone()
    if row[0] > time.time():
        raise ValueError("Shared Binance cooldown active; do not send research requests")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["freeze", "prepare", "spot", "futures"])
    args = parser.parse_args()
    native = configure()
    if args.stage == "freeze":
        freeze()
    else:
        verify()
        if args.stage == "prepare":
            intake.main()
        else:
            native.run(args.stage)


if __name__ == "__main__":
    main()
