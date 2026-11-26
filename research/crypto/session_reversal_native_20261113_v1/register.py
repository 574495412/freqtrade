"""Freeze a two-asset, three-policy native funded-session comparison."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/session_reversal_native_20261113_v1"


def ref(path):
    return {"path": str(path), "sha256": sha(path)}


def main():
    if (HERE / "plan.json").exists():
        raise FileExistsError("Preserve frozen campaign")
    parent = json.loads(
        (ROOT / "research/crypto/oi_cascade_source_native_20261113_v1/plan.json").read_text()
    )
    wide = json.loads(
        (ROOT / "research/crypto/strategy_comparison_wide_20261105/plan.json").read_text()
    )
    keys = [
        "engine_source",
        "engine_manifest",
        "engine_commit",
        "environment",
        "market_snapshot",
        "research_support",
        "storage_source",
        "observer_source",
        "start",
        "end",
        "comparison_end",
        "periods",
    ]
    plan = {k: parent[k] for k in keys}
    OUT.mkdir()
    (OUT / "candles/futures").mkdir(parents=True)
    pairs = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    for sym in ["BTC", "ETH"]:
        for suffix in ["5m-futures", "1h-futures", "1h-mark", "1h-funding_rate"]:
            source = (
                ROOT
                / f"user_data/crypto_quant/nfi40_data_v002/candles/futures/{sym}_USDT_USDT-{suffix}.feather"
            )
            os.link(source, OUT / "candles/futures" / source.name)
    save(
        OUT / "data_receipts.json",
        {
            str(p.relative_to(OUT / "candles")): sha(p)
            for p in sorted((OUT / "candles/futures").glob("*.feather"))
        },
    )
    protocol = {
        "registered_at": datetime.now(UTC).isoformat(),
        "source": "Wu/Pinsky 2026 DOI10.3390/jrfm19090692; author repo5693993e108f2b3668bd21b1dd36ae31f79d8fcd",
        "rules": "BTC08/20UTC reverse PREVIOUS SAME session return; ETH05UTC same-session reversal and17UTC long. No own sign/hour selection.",
        "source_selection": "Source BTC full-sample optimum unstable in training holdout; ETH more stable. Paper SPA fails to reject. Do not omit these failures or claim our overlap as OOS.",
        "adaptation": "Binance USDT isolated perpetual instead of Kraken USD hourly spot; source rule from completed hourly boundary quotes. Flat each boundary, reopen exactly5m later via ordinary native5m strategy; actual wallet/lot/fees/funding/liquidation. No external account simulator.",
        "funding_and_risk": "1x,99% native investable funds,one position per separate500 wallet; catastrophic99%stop and native liquidation. Historical50%DD gate is not a live guarantee.",
        "controls": "TimedLong uses identical session boundary,5m cash gap and finite input mask, always long. PassiveLong enters earliest native valid bar,holds until terminal. No same-direction keep or fee-free rebalance invented.",
        "combination": "For each policy sum actual independent BTC500 and ETH500 hourly wallets, no transfers/rebalance; total1000. Never scale a1000 wallet curve.",
        "costs_bps_per_side": [15, 8],
        "window": [plan["start"], plan["comparison_end"]],
        "acceptance": "Combined source must beat both matched timed-long and passive combinations at BOTH costs; positive full and each continuous annual slice; hourlyDD<=50%. Native numeric pass remains conditional, not independent/live qualification.",
        "no_rescue": "No local sign/hour/window/leverage/fee tuning after results. Preserve source and technical failures.",
        "orders_enabled": False,
        "production_eligible": False,
    }
    save(HERE / "protocol.json", protocol)
    candidates = []
    for strategy in ["SourceSessions", "TimedLongSessions", "PassiveLong"]:
        for sym, pair in zip(["BTC", "ETH"], pairs, strict=True):
            name = f"{strategy}_{sym}"
            config = json.loads(Path(parent["candidates"][0]["config"]["path"]).read_text())
            config.pop("timeframe_detail", None)
            config.update(strategy=strategy, dry_run_wallet=500, max_open_trades=1)
            config["exchange"]["pair_whitelist"] = [pair]
            path = HERE / (name + ".json")
            save(path, config)
            candidates.append(
                {
                    "name": name,
                    "strategy": strategy,
                    "capital": 500,
                    "pairs": [pair],
                    "config": ref(path),
                    "source_files": {"SessionRules.py": ref(HERE / "SessionRules.py")},
                }
            )
    plan.update(
        schema="freqtrade.native.session-reversal.v1",
        registered_at=protocol["registered_at"],
        output_root=str(OUT),
        datadir=str(OUT / "candles"),
        data_receipts=ref(OUT / "data_receipts.json"),
        pairs=pairs,
        candidates=candidates,
        marks={p: wide["marks"][p] for p in pairs},
        initial_equity_usdt=500,
        allowed_leverage=1.0,
        total_cost_bps_per_side=[15, 8],
        runner_sha256=sha(HERE / "native_runner.py"),
        runner_module="research.crypto.session_reversal_native_20261113_v1.native_runner",
        memory_guard_bytes=3 * 1024**3,
        wall_guard_seconds=3600,
        additional_source_seals=[
            ref(p)
            for p in [
                HERE / "protocol.json",
                HERE / "source_manifest.json",
                HERE / "run.py",
                HERE / "preflight.py",
                ROOT / "research/crypto/band_trend_native_20261113_v1/observe_clock.py",
                ROOT / "research/crypto/skew_vol_native_20261113_v1/clock_fix.py",
                *sorted((HERE / "source").glob("*")),
            ]
        ],
        orders_enabled=False,
        production_eligible=False,
    )
    save(HERE / "plan.json", plan)
    print(json.dumps({"registered_native_accounts": 12, "financial_results_seen": False}))


if __name__ == "__main__":
    main()
