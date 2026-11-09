"""Freeze public inputs and causal schedules; no account or profit calculation."""

import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import rapidjson

from research.crypto.nfi80_capacity_20261109 import study as base


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/nfi80_historical_universe_20261109_v001"
OLD = base.OUT
START = base.START
END = pd.Timestamp(base.read(base.PROTOCOL)["end"])
PLAN = base.read(base.HERE / "L3_S12-v003.json")
save, read, ref, sha = base.save, base.read, base.ref, base.sha


def daily(pair):
    folder = OUT / "daily"
    folder.mkdir(exist_ok=True)
    path = folder / (pair.split("/")[0] + ".json")
    if not path.exists():
        rows = base.get(
            "https://fapi.binance.com/fapi/v1/klines",
            {
                "symbol": pair.split(":")[0].replace("/", ""),
                "interval": "1d",
                "startTime": int((START - pd.Timedelta(days=800)).timestamp() * 1000),
                "endTime": int(END.timestamp() * 1000) - 1,
                "limit": 1500,
            },
        ).json()
        if not isinstance(rows, list) or not rows or len(rows) >= 1500:
            raise ValueError("Daily response missing/truncated: " + pair)
        save(path, rows)
    rows = read(path)
    rows = [r for r in rows if r[6] < int(END.timestamp() * 1000)]
    f = pd.DataFrame(
        {
            "date": pd.to_datetime([r[0] for r in rows], unit="ms", utc=True),
            "close_time": pd.to_datetime([r[6] for r in rows], unit="ms", utc=True),
            "quote_volume": [float(r[7]) for r in rows],
            **{n: [float(r[i]) for r in rows] for i, n in enumerate(base.COLS[1:], 1)},
        }
    )
    expected = pd.date_range(f.date.min(), END.normalize(), freq="D", inclusive="left")
    if (
        not pd.DatetimeIndex(f.date).as_unit("ns").equals(expected.as_unit("ns"))
        or not np.isfinite(f[[*base.COLS[1:], "quote_volume"]]).all().all()
        or (f.close <= 0).any()
        or (f.quote_volume < 0).any()
    ):
        raise ValueError("Invalid daily history: " + pair)
    return pair, f


def schedule():
    if (HERE / "plans.json").exists():
        raise FileExistsError("Registered schedules are immutable; use a separate study revision")
    OUT.mkdir(exist_ok=True)

    def parse(p):
        return rapidjson.loads(p.read_text(), parse_mode=3)

    from freqtrade.plugins.pairlist.pairlist_helpers import expand_pairlist

    universe = parse(base.HERE / "author-universe.json")["exchange"]["pair_whitelist"]
    blocked = set(
        expand_pairlist(
            parse(base.HERE / "author-blacklist.json")["exchange"]["pair_blacklist"], universe
        )
    )
    markets = read(PLAN["original_full_market_snapshot"]["path"])
    eligible = [
        p
        for p in universe
        if p not in blocked
        and markets["markets"].get(p, {}).get("active")
        and markets["leverage_tiers"].get(p)
    ]
    frames = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for job in as_completed([pool.submit(daily, p) for p in eligible]):
            pair, frame = job.result()
            frames[pair] = frame
            print({"stage": "daily", "complete": len(frames), "total": len(eligible)}, flush=True)
    days = {}
    for date in pd.date_range(START, END.normalize(), freq="D"):
        ranked = []
        for pair, f in frames.items():
            history = f.loc[f.close_time < date]
            prior = history.loc[history.date == date - pd.Timedelta(days=1)]
            if len(history) >= 60 and len(prior) == 1 and prior.quote_volume.iloc[0] > 0:
                ranked.append((pair, float(prior.quote_volume.iloc[0]), prior.close_time.iloc[0]))
        ranked.sort(key=lambda r: (-r[1], r[0]))
        if len(ranked) < 80:
            raise ValueError("Insufficient historical candidates: " + str(date))
        top = ranked[:80]
        days[date.date().isoformat()] = {
            "pairs": [r[0] for r in top],
            "latest_input_close": max(r[2] for r in top).isoformat(),
            "ranked_quote_volume": {p: v for p, v, _ in ranked},
            "eligible_count": len(ranked),
        }
    union = sorted({p for row in days.values() for p in row["pairs"]})
    result = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "days": days,
        "union": union,
        "candidate_count": len(eligible),
        "daily_sources": {p: ref(OUT / "daily" / (p.split("/")[0] + ".json")) for p in eligible},
    }
    save(OUT / "schedule.json", result)
    initial = days[START.date().isoformat()]["pairs"]
    save(
        HERE / "schedule-summary.json",
        {
            "schedule": ref(OUT / "schedule.json"),
            "days": len(days),
            "union_count": len(union),
            "candidate_count": len(eligible),
            "initial_pairs": initial,
            "initial_overlap_legacy": len(set(initial) & set(PLAN["pairs"])),
            "additions": sum(
                len(set(b["pairs"]) - set(a["pairs"]))
                for a, b in zip(list(days.values())[:-1], list(days.values())[1:], strict=True)
            ),
            "protocol": ref(HERE / "PROTOCOL.md"),
        },
    )
    for pair, f in frames.items():
        f.to_parquet(OUT / "daily" / (pair.split("/")[0] + ".parquet"), index=False)
    print(read(HERE / "schedule-summary.json"), flush=True)


def execution_pair(pair):
    from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
    from freqtrade.enums import CandleType

    stem = pair.replace("/", "_").replace(":", "_")
    target = OUT / "candles/futures"
    symbol = pair.split(":")[0].replace("/", "")
    done = OUT / "raw" / symbol / "admitted.json"
    if done.exists():
        row = read(done)
        if all(sha(OUT / "candles" / n) == h for n, h in row["candles"].items()):
            return row
        raise ValueError("Prepared data changed: " + pair)
    if pair in PLAN["pairs"]:
        for p in (OLD / "candles/futures").glob(stem + "-*.feather"):
            dest = target / p.name
            if not dest.exists():
                os.link(p, dest)
        row = {
            "pair": pair,
            "reused_frozen_inputs": True,
            "candles": {"futures/" + p.name: sha(p) for p in target.glob(stem + "-*.feather")},
        }
        done.parent.mkdir(parents=True, exist_ok=True)
        save(done, row)
        return row
    # Reuse the source-faithful public intake, with a new output/protocol only.
    base.OUT = OUT
    base.PROTOCOL = HERE / "input-protocol.json"
    prepared = OUT / "raw" / symbol / "completed.json"
    if prepared.exists():
        row = read(prepared)
        for name, digest in row["candles"].items():
            if not name.endswith("-1d-futures.feather") and sha(OUT / "candles" / name) != digest:
                raise ValueError("Prepared price/funding changed: " + pair)
    else:
        row = base.prepare_pair(pair)
    frame = pd.read_parquet(OUT / "daily" / (pair.split("/")[0] + ".parquet"))
    FeatherDataHandler(OUT / "candles").ohlcv_store(
        pair, "1d", frame[base.COLS], CandleType.FUTURES
    )
    row["candles"]["futures/" + stem + "-1d-futures.feather"] = sha(
        target / (stem + "-1d-futures.feather")
    )
    row["daily_input"] = ref(OUT / "daily" / (pair.split("/")[0] + ".json"))
    done.parent.mkdir(parents=True, exist_ok=True)
    save(done, row)
    return row


def execution():
    if (HERE / "plans.json").exists():
        raise FileExistsError("Registered execution inputs are immutable")
    selection = read(OUT / "schedule.json")
    (OUT / "candles/futures").mkdir(parents=True, exist_ok=True)
    save(
        HERE / "input-protocol.json",
        {**read(base.HERE / "protocol.json"), "pairs": selection["union"]},
    )
    rows = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for job in as_completed([pool.submit(execution_pair, p) for p in selection["union"]]):
            row = job.result()
            rows[row["pair"]] = row
            save(
                OUT / "intake-progress.json",
                {"complete": len(rows), "total": len(selection["union"]), "last": row["pair"]},
            )
            print(
                {
                    "stage": "execution",
                    "complete": len(rows),
                    "total": len(selection["union"]),
                    "pair": row["pair"],
                },
                flush=True,
            )
    save(OUT / "admission.json", rows)
    save(
        OUT / "data-receipts.json",
        {n: h for row in rows.values() for n, h in row["candles"].items()},
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=["schedule", "execution"])
    globals()[p.parse_args().stage]()
