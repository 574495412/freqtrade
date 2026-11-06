"""Replay the unchanged A02 hypothesis on a disjoint earlier calendar year."""

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, read_archive, save, sha
from research.crypto import download_nfi_spot_20261105 as downloader
from research.crypto.basis_flush_20261107 import run as study
from research.crypto.basis_flush_20261107.verify import confirm_pulses
from research.crypto.breadth_diffusion_20261107 import verify as account_audit
from research.crypto.trend_mainline.account import replay


HERE = Path(__file__).resolve().parent
OUT = study.DATA / "basis_flush_2024_v001"
FROZEN = HERE / "extension_2024_registration.json"


def register():
    if FROZEN.exists() or OUT.exists():
        raise FileExistsError("Earlier-window check already registered")
    paths = (Path(__file__), Path(downloader.__file__), study.PLAN, HERE / "result.json")
    save(
        FROZEN,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "sources": {str(path.relative_to(study.ROOT)): sha(path) for path in paths},
            "warmup_start": "2023-12-01",
            "start": "2024-01-01",
            "split": "2024-07-01",
            "end": "2025-01-01",
            "policy": (
                "Run the same symbols, signals, costs, liquidity, risk and execution. "
                "Reset the account to cash at the earlier start. Report both costs and halves. "
                "This disjoint earlier window is a retrospective replication after the "
                "2025–26 result. Preserve the original failed promotion screen."
            ),
        },
    )


def run():
    protocol = read(FROZEN)
    parent = read(study.PLAN)
    for path, digest in {**parent["checksums"], **protocol["sources"]}.items():
        if sha(study.ROOT / path) != digest:
            raise ValueError(f"Frozen source changed: {path}")
    plan = {**parent, **{k: protocol[k] for k in ("warmup_start", "start", "split", "end")}}
    OUT.mkdir()
    save(OUT / "attempt.json", {"registration_sha256": sha(FROZEN)})
    downloader.OUT = OUT
    months = pd.period_range("2023-12", "2024-12", freq="M")
    jobs = [(s, "5m", str(month)) for s in plan["symbols"] for month in months]
    with ThreadPoolExecutor(max_workers=12) as pool:
        receipts = list(pool.map(downloader.download, jobs))
    save(OUT / "spot_sources.json", receipts)
    grid = pd.date_range(plan["warmup_start"], plan["end"], inclusive="left", freq="5min", tz="UTC")
    hours = grid[::12]
    funding_grid = pd.date_range(plan["start"], plan["end"], inclusive="left", freq="8h", tz="UTC")
    panel = {}
    for symbol in plan["symbols"]:
        files = [item for item in receipts if item["symbol"] == symbol]
        if any(sha(Path(item["path"])) != item["sha256"] for item in files):
            raise ValueError("Checksum mismatch after download")
        spot = pd.concat([read_archive(Path(item["path"])) for item in files]).set_index("date")
        bars = pd.read_parquet(study.PERP / f"{symbol}_5m.parquet").loc[grid]
        features = pd.read_parquet(study.PERP / f"{symbol}_features.parquet").loc[hours]
        funding = pd.read_parquet(study.PERP / f"{symbol}_funding.parquet").loc[funding_grid]
        if not (spot.index.as_unit("us").equals(grid) and bars.index.equals(grid)):
            raise ValueError(f"Spot/perpetual alignment failed: {symbol}")
        spot.index = grid
        panel[symbol] = {"bars": bars, "spot": spot.close, "features": features, "funding": funding}

    # Independently check all pulses before starting any account.
    by_case = {
        case: {
            s: {**item, "features": study.signals(item, plan["signal"], case)}
            for s, item in panel.items()
        }
        for case in plan["cases"]
    }
    pulses = {
        case: pd.DataFrame({s: item["features"].momentum for s, item in inputs.items()})
        for case, inputs in by_case.items()
    }
    for s, item in panel.items():
        confirm_pulses(plan, pulses, s, item["bars"], item["spot"], item["features"])
    signal_hashes = {}
    for case, signal in pulses.items():
        path = OUT / f"{case}-signals.parquet"
        signal.to_parquet(path)
        signal_hashes[case] = sha(path)

    account_audit.OUT = OUT
    marks = {s: (item["bars"], item["funding"]) for s, item in panel.items()}
    results, audit = {}, {}
    for case, inputs in by_case.items():
        for slip in plan["extra_cost_bps_per_side"]:
            name = f"{case}_{5 + slip}bps"
            account = replay(
                inputs, plan, "Momentum63", plan["capital_usdt"], slip, plan["start"], plan["end"]
            )
            path = OUT / name
            path.mkdir()
            account["curve"].to_parquet(path / "nav.parquet", index=False)
            for field in ("events", "decisions", "trades", "state"):
                save(path / f"{field}.json", account[field])
            results[name] = study.summarize(account, plan)
            audit[name] = account_audit.audit_account(plan, marks, name)
            print(name, results[name]["net_return"], results[name]["closed_trades"], flush=True)
    save(
        HERE / "extension_2024_result.json",
        {
            "registration_sha256": sha(FROZEN),
            "official_spot_archive_count": len(receipts),
            "spot_receipts_sha256": sha(OUT / "spot_sources.json"),
            "signal_sha256": signal_hashes,
            "accounts": results,
            "audit": audit,
            "promotion_changed": False,
        },
    )


if __name__ == "__main__":
    {"register": register, "run": run}[sys.argv[1]]()
