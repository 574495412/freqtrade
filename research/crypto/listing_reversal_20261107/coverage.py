"""Sensitivity of frozen A05 accounts to recovering the fourteen intake exclusions."""

import math
import sys
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, read_archive, save, sha
from research.crypto.listing_reversal_20261107 import audit, replay
from research.crypto.listing_reversal_20261107.catalog import HERE, OUT, fetch, now
from research.crypto.listing_reversal_20261107.intake import ADMISSION, FUNDING_URL


MARK = "https://fapi.binance.com/fapi/v1/markPriceKlines"
STAGE = OUT / "coverage_recovery"
PROTOCOL = HERE / "coverage_registration.json"
BOUND = {
    "intake": OUT / "admission.json",
    "intake_plan": ADMISSION,
    "replay": Path(replay.__file__),
    "parent_plan": replay.PLAN,
    "audit": Path(audit.__file__),
    "source": Path(__file__),
    "original": HERE / "result.json",
    "reset": HERE / "reset_2026_result.json",
    "reset_plan": HERE / "reset_2026_registration.json",
}


def register():
    if PROTOCOL.exists() or STAGE.exists():
        raise FileExistsError("Coverage sensitivity already registered")
    intake = read(OUT / "admission.json")
    unknown = [r["symbol"] for r in intake["rows"] if r["state"] == "UNKNOWN"]
    save(
        PROTOCOL,
        {
            "registered_utc": now(),
            "hashes": {key: sha(path) for key, path in BOUND.items()},
            "symbols": unknown,
            "mark_endpoint": MARK,
            "policy": (
                "Recover all excluded inputs in the initial registered cohort. Only discard a "
                "nonpositive settlement mark at or within the first five minutes of launch, "
                "before any position can exist. Keep raw responses. Missing mark archives may "
                "be replaced with an exact continuous Binance mark API response. No economics "
                "are used to decide which names to recover. Preserve both original runs."
            ),
        },
    )


def trade_from_archives(row, source, grid):
    parts = []
    for item in row["archive_receipts"]:
        if item["state"] == "VERIFIED" and item["file"].startswith("klines-"):
            path = source / item["file"]
            audit.digest(path, item["sha256"])
            parts.append(read_archive(path))
    data = pd.concat(parts)
    data["date"] = data.date.dt.as_unit("us")
    span = data.loc[data.date.between(grid[0], grid[-1])].drop_duplicates()
    if span.date.duplicated().any():
        raise ValueError("Overlapping raw trade archives disagree")
    result = span.set_index("date").sort_index()
    if not result.index.equals(grid):
        raise ValueError("Missing trade bars")
    return result


def mark_from_source(row, source, target, grid, start_ms, end_ms):
    frames = []
    for item in row["archive_receipts"]:
        if item["state"] == "VERIFIED" and item["file"].startswith("markPriceKlines-"):
            path = source / item["file"]
            audit.digest(path, item["sha256"])
            frames.append(read_archive(path))
    if frames:
        data = pd.concat(frames).set_index("date").sort_index()
        data.index = data.index.as_unit("us")
        data = data.loc[grid[0] : grid[-1]]
    else:
        params = {
            "symbol": row["symbol"],
            "interval": "5m",
            "startTime": start_ms,
            "endTime": end_ms - 1,
            "limit": 1500,
        }
        path = target / "mark-api.json"
        path.write_bytes(fetch(MARK, params))
        native = read(path)
        data = pd.DataFrame(
            {
                "date": pd.to_datetime([r[0] for r in native], unit="ms", utc=True).as_unit("us"),
                "high": [float(r[2]) for r in native],
                "close": [float(r[4]) for r in native],
            }
        ).set_index("date")
        if any(r[6] != r[0] + 299_999 for r in native):
            raise ValueError("Mark API close clocks differ from 5m grid")
    if not data.index.equals(grid) or not np.isfinite(data[["high", "close"]]).all().all():
        raise ValueError("Incomplete mark inputs")
    if not (data.high >= data.close).all() or not (data.close > 0).all():
        raise ValueError("Invalid mark values")
    return data


def funding_from_source(symbol, source, target, start_ms, end_ms):
    raw = source / "funding.json"
    path = target / "funding.json"
    if raw.exists():
        path.write_bytes(raw.read_bytes())
    else:
        params = {"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000}
        path.write_bytes(fetch(FUNDING_URL, params))
    rows = read(path)
    if not isinstance(rows, list) or not 0 < len(rows) < 1000:
        raise ValueError("Unexpected funding history")
    retained, discarded = [], []
    for row in rows:
        at = row["fundingTime"]
        mark, rate = float(row["markPrice"]), float(row["fundingRate"])
        if row["symbol"] != symbol or not start_ms <= at <= end_ms or not math.isfinite(rate):
            raise ValueError("Invalid funding identity or clock")
        if math.isfinite(mark) and mark > 0:
            retained.append((at, rate, mark))
        elif at <= start_ms + 300_000:
            discarded.append(at)
        else:
            raise ValueError("Funding mark is invalid after launch")
    clocks = [x[0] for x in retained]
    if not clocks or any(not 0 < b - a <= 9 * 3_600_000 for a, b in pairwise(clocks)):
        raise ValueError("Funding grid incomplete")
    if max(clocks[0] - start_ms, end_ms - clocks[-1]) > 9 * 3_600_000:
        raise ValueError("Funding coverage boundary incomplete")
    frame = pd.DataFrame(retained, columns=["date", "rate", "mark"])
    frame["date"] = pd.to_datetime(frame.date, unit="ms", utc=True).dt.as_unit("us")
    return frame.set_index("date"), discarded


def recover(row):
    symbol = row["symbol"]
    source = OUT / "data" / symbol
    target = STAGE / "data" / symbol
    target.mkdir()
    launch = pd.Timestamp(row["launch_utc"])
    end = launch + pd.Timedelta(hours=120)
    start_ms, end_ms = int(launch.timestamp() * 1000), int(end.timestamp() * 1000)
    grid = pd.date_range(launch, end, freq="5min", inclusive="left").as_unit("us")
    trade = trade_from_archives(row, source, grid)
    mark = mark_from_source(row, source, target, grid, start_ms, end_ms)
    funding, ignored = funding_from_source(symbol, source, target, start_ms, end_ms)
    receipt = {
        "symbol": symbol,
        "launch_utc": launch.isoformat(),
        "state": "ADMITTED",
        "pre_entry_invalid_funding_clock_ms": ignored,
    }
    for name, frame in (("trade", trade), ("mark", mark), ("funding", funding)):
        path = target / f"{name}.parquet"
        frame.to_parquet(path)
        receipt[f"{name}_sha256"] = sha(path)
    receipt["funding_raw_sha256"] = sha(target / "funding.json")
    if (target / "mark-api.json").exists():
        receipt["mark_api_sha256"] = sha(target / "mark-api.json")
    return receipt, (trade, mark, funding, replay.bar_number(launch))


def run():
    protocol = read(PROTOCOL)
    for name, path in BOUND.items():
        audit.digest(path, protocol["hashes"][name])
    (STAGE / "data").mkdir(parents=True)
    intake = read(OUT / "admission.json")
    base_plan = read(replay.PLAN)
    panel = audit.audit_sources()
    markets = {item.name: item for item in replay.market_data(base_plan, intake)}
    recovered = []
    failed = {}
    for row in intake["rows"]:
        if row["symbol"] in protocol["symbols"]:
            try:
                receipt, frames = recover(row)
            except (OSError, ValueError, KeyError) as error:
                failed[row["symbol"]] = str(error)
            else:
                recovered.append(receipt)
                panel[row["symbol"]] = frames
    replay.OUT = STAGE
    markets.update(
        {m.name: m for m in replay.market_data(base_plan, {**intake, "rows": recovered})}
    )
    original_reset = read(HERE / "reset_2026_registration.json")
    reset_plan = {
        **base_plan,
        **{k: original_reset[k] for k in ("common_start", "common_end", "split")},
    }
    records = {}
    for label, plan in (("continuous", base_plan), ("reset_2026", reset_plan)):
        directory = STAGE / label
        directory.mkdir()
        audit.OUT = directory
        start = replay.bar_number(pd.Timestamp(plan["common_start"], tz="UTC"))
        selected = {name: m for name, m in markets.items() if m.launch >= start}
        for case in plan["cases"]:
            for cost in plan["cost_bps"]:
                key = f"{case}_{cost}bps"
                account, n = replay.simulate(plan, selected, case, cost)
                nav, summary = replay.summarize(plan, account, n)
                path = directory / key
                path.mkdir()
                nav.to_parquet(path / "nav.parquet", index=False)
                save(path / "events.json", account.events)
                save(path / "closed.json", account.finished)
                verified = audit.audit_account(plan, panel, key, summary)
                records[f"{label}/{key}"] = {"summary": summary, "audit": verified}
                print(label, key, summary["net_return"], summary["max_drawdown"], flush=True)
    save(
        HERE / "coverage_result.json",
        {
            "registration_sha256": sha(PROTOCOL),
            "recovered": recovered,
            "unresolved": failed,
            "accounts": records,
            "original_gate_changed": False,
        },
    )


if __name__ == "__main__":
    {"register": register, "run": run}[sys.argv[1]]()
