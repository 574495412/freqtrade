"""Freeze a local six-major transfer; secret-filter source returns not adopted."""

import json
from datetime import UTC, datetime
from pathlib import Path

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/spread_liquidity_native_20261114_v1"


def ref(path):
    return {"path": str(path), "sha256": sha(path)}


def main():
    if (HERE / "plan.json").exists():
        raise FileExistsError("Frozen registration exists")
    admission = json.loads((HERE / "input_admission.json").read_text())
    if not admission["passed"]:
        raise ValueError("No admitted input")
    old = json.loads(
        (ROOT / "research/crypto/skew_vol_native_20261113_v1/v002/plan.json").read_text()
    )
    pairs = [f"{s}/USDT:USDT" for s in ["BTC", "ETH", "BNB", "XRP", "LTC", "DOGE"]]
    protocol = {
        "registered_at": datetime.now(UTC).isoformat(),
        "source_estimator": "eguidotti/bidask1caba55d63ebab6c855536be51c43bdfc48d2dec,MIT,point estimator defaultsignFalse30completed dailyOHLC observations",
        "source_rule": "Public yuxinsun.eth2025-04-07 post long high estimatedspread,short low,30day window,24hrebalance. Secret filter is unavailable; no source10x return reproduction.",
        "local_universe": pairs,
        "universe_limits": "Fixed six-major transfer selected before factor outputs. Not original all-Binance spot/perp pool,not point-in-time eligibility certification or replacement for prior40pair results.",
        "signals": "Within native IStrategy/DataProvider,rank each model on6pairs using completed previousUTC daily inputs. Long highest,short lowest;single sorted(score,pair) list for distinct deterministic ties. Twoof6not exact10%source deciles.",
        "controls": "Amihud30 mean absolute simple dailyreturn / ACTUAL quote-volume inUSDT;LowVol63negative sampleSD. Same universe/clock/nativecoststake allocation;not additional published winning strategies.",
        "native_policy": "1hdecision row00holds only already-known prior-day data,nextnative01:00open;native5mdetail. Same-side quantity persists until rankchanges,no free daily constant-weight reset or DCA. Native unlimitedcash,max2positions,99%tradable funds;reject minimum-stake upsizing and late entries.",
        "allocation_limit": "Approximately equal native cost-stake slots,not guaranteed equal markedlongshort,beta-neutrality or constant1xgross.1xisolated setting,99%catastrophicstop andnative liquidation.",
        "comparison": "2025-01-01T00:00Z to2026-08-31T23:00Z continuous,annual slices same wallet. Eachmodel1000/500 at15/8bps. Combination only sum actualEdge500+LowVol500;no transfers or scaled1000 paths.",
        "funding": "Native inclusive model;normal entry/exit01:00 avoids same nominal8h funding handoff. Full6coin raw8h events/rates checked; private payment/time order not certified.",
        "acceptance": "Edge andEdge/LowVol combo positive full andeachcontinuousyear,DD<=50%,above standalone controls atBOTHcosts. Combinationmust beat best standalone1000return. Numericpass alone requires independent evidence before anyqualification.",
        "no_rescue": "No sourcewindow/sign/cost/universe/weights changes after outcomes; preserve old failures and source omissions.",
        "orders_enabled": False,
        "production_eligible": False,
    }
    save(HERE / "protocol.json", protocol)
    candidates = []
    for strategy in ["EdgeLiquidity", "AmihudLiquidity", "LowVolBaseline"]:
        for capital in [1000, 500]:
            name = f"{strategy}_{capital}"
            config = json.loads(Path(old["candidates"][0]["config"]["path"]).read_text())
            config = {k: v for k, v in config.items() if not k.startswith("research_")}
            config.update(
                strategy=strategy,
                dry_run_wallet=capital,
                max_open_trades=2,
                timeframe_detail="5m",
                research_quote_volume=admission["quote_input"]["path"],
                research_quote_sha256=admission["quote_input"]["sha256"],
            )
            config["exchange"]["pair_whitelist"] = pairs
            path = HERE / (name + ".json")
            save(path, config)
            candidates.append(
                {
                    "name": name,
                    "strategy": strategy,
                    "capital": capital,
                    "pairs": pairs,
                    "config": ref(path),
                    "source_files": {
                        n: ref(HERE / n)
                        for n in ["SpreadPortfolio.py", "SpreadFeatures.py", "edge_author.py"]
                    },
                }
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
        "allowed_leverage",
        "memory_guard_bytes",
        "wall_guard_seconds",
    ]
    plan = {k: old[k] for k in keys}
    plan.update(
        schema="freqtrade.native.spread-liquidity.v1",
        registered_at=protocol["registered_at"],
        output_root=str(OUT),
        datadir=str(OUT / "candles"),
        data_receipts=admission["data_receipts"],
        pairs=pairs,
        marks={p: old["marks"][p] for p in pairs},
        candidates=candidates,
        initial_equity_usdt=1000,
        total_cost_bps_per_side=[15, 8],
        runner_sha256=sha(HERE / "native_runner.py"),
        runner_module="research.crypto.spread_liquidity_native_20261114_v1.native_runner",
        additional_source_seals=[
            ref(p)
            for p in [
                HERE / "protocol.json",
                HERE / "source_manifest.json",
                HERE / "input_admission.json",
                HERE / "preflight.py",
                HERE / "run.py",
                Path(admission["quote_input"]["path"]),
                ROOT / "research/crypto/band_trend_native_20261113_v1/observe_clock.py",
                ROOT / "research/crypto/skew_vol_native_20261113_v1/clock_fix.py",
            ]
        ],
        orders_enabled=False,
        production_eligible=False,
    )
    if sha(HERE / "edge_author.py") != sha(HERE / "source/bidask/python/bidask/edge.py"):
        raise ValueError("Author estimator differs")
    save(HERE / "plan.json", plan)
    print(json.dumps({"registered_accounts": 12, "financial_outcomes_seen": False}))


if __name__ == "__main__":
    main()
