"""Freeze the source, causal feature contract and actual native account matrix."""

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from research.crypto.skew_vol_native_20261113_v1.features import monthly_targets, observations
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/skew_vol_native_20261113_v1"


def ref(path):
    return {"path": str(path), "sha256": sha(path)}


def main():
    if (HERE / "plan.json").exists() or OUT.exists():
        raise FileExistsError("Never overwrite a frozen research attempt")
    OUT.mkdir()
    url = "https://quantpedia.com/skewness-lottery-trading-strategy-in-cryptocurrencies/"
    response = requests.get(url, proxies={"https": "http://127.0.0.1:7897"}, timeout=25)
    response.raise_for_status()
    source = HERE / "source_article.html"
    source.write_bytes(response.content)
    for text in ["360", "rebalanced monthly", "lowest", "highest"]:
        if text not in response.text:
            raise ValueError("Source text missing an expected rule")
    save(
        HERE / "source_receipt.json",
        {
            "url": url,
            "retrieved_at": datetime.now(UTC).isoformat(),
            **ref(source),
            "source_is_original_own_research": True,
            "source_code": "Premium code not obtained; local strategy adaptation, not a port.",
            "claim_scope": "Annual360day skewness, long low/short high, monthly quartiles.30day source sign reversal is not tested.",
        },
    )
    previous = ROOT / "research/crypto/band_trend_native_20261113_v1/v003/plan.json"
    parent = json.loads(previous.read_text())
    keys = [
        "engine_source",
        "engine_manifest",
        "engine_commit",
        "datadir",
        "data_receipts",
        "market_snapshot",
        "environment",
        "research_support",
        "storage_source",
        "observer_source",
        "marks",
        "pairs",
        "periods",
        "start",
        "end",
        "comparison_end",
        "additional_source_seals",
    ]
    plan = {key: parent[key] for key in keys}
    dates = pd.date_range("2025-01-01", "2026-08-01", freq="MS", tz="UTC")
    factor_path = OUT / "monthly_scores.json"
    # This contract is written before any signal diagnostic or new account return.
    save(
        HERE / "protocol.json",
        {
            "registered_at": datetime.now(UTC).isoformat(),
            "source": ref(HERE / "source_receipt.json"),
            "source_formula": "Population skewness of360complete daily simple returns; adjusted-sample multiplier constant n360 so ranks identical.",
            "lowvol_control": "63complete daily simple-return sample standard deviation; common361close input support.",
            "selection": "Fixed40; exact10lowest long,10highest short, tie by pair; missing input rejects month, not delete names.",
            "calendar": "Month-start decision uses last completed daily bar; close all on day1, one initial attempt at00:00day2; hold to next day1. Native daily signals lag once.",
            "sizing": "Each native wallet freezes0.99available equity/20 at first monthly entry; no min-order upsizing; unfilled budgets remain cash.",
            "account_window": [plan["start"], plan["comparison_end"]],
            "initial_wallets": [1000, 500],
            "costs_bps_per_side": [15, 8],
            "native_risk": "1x isolated,20slots,noDCA,noROI,no trailing; native99%per-position catastrophic stop and native liquidation. No portfolio50%hard stop.",
            "combination": "Sum two actual500USDT native wallets; no transfers or rebalancing; max40positions, duplicate same-contract sleeves possible.",
            "acceptance": "Both costs: combination positive, return above both1000wallet controls, hourlyDD<=50%, both continuous calendar slices positive; no retuning.",
            "source_adaptations": [
                "Coinmetrics USD spot45 replaced by fixed Binance USDT perpetual40; no original-universe replication.",
                "Explicit one-day monthly cash gap; minimum orders, native price precision, margin/funding and catastrophic risk change economics.",
                "Source does not specify skew estimator; frozen moment estimator and common n make conventional correction rank-invariant.",
            ],
            "independent_oos": False,
            "orders_enabled": False,
        },
    )
    frames = {}
    input_receipts = {}
    for pair in plan["pairs"]:
        stem = pair.replace("/", "_").replace(":", "_")
        path = Path(plan["datadir"]) / "futures" / f"{stem}-1d-futures.feather"
        frames[pair] = pd.read_feather(path)
        if frames[pair].date.duplicated().any():
            raise ValueError("Duplicate source daily bars")
        input_receipts[pair] = ref(path)
    months, rows, audit = {}, [], []
    for decision in dates:
        current = [{"pair": pair, **observations(frames[pair], decision)} for pair in plan["pairs"]]
        months[decision.strftime("%Y-%m")] = {
            key: monthly_targets(current, key) for key in ["skew360", "volatility63"]
        }
        rows += current
        for row in current:
            pair = row["pair"]
            # Independent scalar moment/SD arithmetic from exact indexed closes.
            raw = (
                frames[pair]
                .set_index("date")
                .close.loc[decision - pd.Timedelta(days=361) : decision - pd.Timedelta(days=1)]
                .tolist()
            )
            returns = [raw[i] / raw[i - 1] - 1 for i in range(1, len(raw))]
            mean = math.fsum(returns) / 360
            second = math.fsum((x - mean) ** 2 for x in returns) / 360
            third = math.fsum((x - mean) ** 3 for x in returns) / 360
            value = third / second**1.5
            recent = returns[-63:]
            center = math.fsum(recent) / 63
            vol = math.sqrt(math.fsum((x - center) ** 2 for x in recent) / 62)
            if abs(value - row["skew360"]) > 1e-10 or abs(vol - row["volatility63"]) > 1e-12:
                raise ValueError("Independent source feature mismatch")
            audit.append(
                {
                    "pair": pair,
                    "decision": str(decision),
                    "skew_abs_error": abs(value - row["skew360"]),
                    "vol_abs_error": abs(vol - row["volatility63"]),
                }
            )
    save(factor_path, {"months": months, "rows": rows, "protocol": ref(HERE / "protocol.json")})
    save(
        HERE / "feature_audit.json",
        {
            "passed": True,
            "observations": audit,
            "monthly_decisions": len(months),
            "source_input_files": input_receipts,
        },
    )
    correlations = []
    for decision in dates:
        part = pd.DataFrame([row for row in rows if row["decision_at"] == str(decision)])
        correlations.append(
            {
                "decision": str(decision),
                "pearson": float(part.skew360.corr(part.volatility63)),
                "spearman": float(part.skew360.rank().corr(part.volatility63.rank())),
            }
        )
    save(
        HERE / "mechanism_overlap.json",
        {
            "scope": "Annual skewness vs63day volatility only, not all existing factor inventory.",
            "monthly": correlations,
            "mean_absolute_pearson": float(np.mean([abs(row["pearson"]) for row in correlations])),
            "no_independent_alpha_claim": True,
        },
    )
    candidates = []
    for strategy in ["AnnualSkew", "LowVolControl"]:
        for capital in [1000, 500]:
            name = f"{strategy}_{capital}"
            config = json.loads(Path(parent["candidates"][0]["config"]["path"]).read_text())
            config.update(
                strategy=strategy,
                dry_run_wallet=capital,
                max_open_trades=20,
                research_monthly_scores=str(factor_path),
                research_scores_sha256=sha(factor_path),
            )
            path = HERE / (name + ".json")
            save(path, config)
            candidates.append(
                {
                    "name": name,
                    "strategy": strategy,
                    "capital": capital,
                    "config": ref(path),
                    "source_files": {"SkewVolPortfolio.py": ref(HERE / "SkewVolPortfolio.py")},
                }
            )
    plan.update(
        schema="freqtrade.native.skew-vol.v1",
        registered_at=datetime.now(UTC).isoformat(),
        output_root=str(OUT),
        native_start="2024-12-31T00:00:00Z",
        candidates=candidates,
        initial_equity_usdt=1000,
        allowed_leverage=1.0,
        total_cost_bps_per_side=[15, 8],
        memory_guard_bytes=3 * 1024**3,
        wall_guard_seconds=3600,
        runner_sha256=sha(HERE / "native_runner.py"),
        protocol=ref(HERE / "protocol.json"),
        required_previous_batch_terminal="band_trend_native_20261113_v1.v003",
        original_source_receipt=ref(HERE / "source_receipt.json"),
        new_validated_factors=0,
        orders_enabled=False,
        production_eligible=False,
    )
    plan["additional_source_seals"] += [
        ref(p)
        for p in [
            HERE / "features.py",
            HERE / "feature_audit.json",
            HERE / "SkewVolPortfolio.py",
            factor_path,
            HERE / "protocol.json",
        ]
    ]
    save(HERE / "plan.json", plan)
    print(json.dumps({"accounts": 8, "monthly_inputs": len(rows), "orders_enabled": False}))


if __name__ == "__main__":
    main()
