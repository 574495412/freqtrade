"""Actual archived native campaigns and full hourly curves; no new account run."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd


HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]
SLOT = 1782273600000
PAIRS = ("AXS/USDT:USDT", "TRB/USDT:USDT")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def stats_from(archive):
    with ZipFile(archive) as z:
        assert z.testzip() is None
        names = [n for n in z.namelist() if n.endswith(".json") and not n.endswith("_config.json")]
        payloads = [json.loads(z.read(n)) for n in names]
        result = [v for v in payloads if "strategy" in v]
    assert len(result) == 1 and len(result[0]["strategy"]) == 1
    return next(iter(result[0]["strategy"].values()))


def exposure(trades, pair, slot=SLOT):
    """Gross native campaigns; no long/short netting or missing-rate assumptions.

    Boundary fills remain explicitly ambiguous. Closed campaign serialization
    residuals are retained separately, never treated as active native positions.
    """
    held, bracket, boundaries, residuals, distances, seen = [], [], [], [], [], set()
    for number, trade in enumerate(trades):
        if trade["pair"] != pair:
            continue
        opened, closed = int(trade["open_timestamp"]), int(trade["close_timestamp"])
        assert opened <= closed
        quantity = D(0)
        for order_index, order in enumerate(trade["orders"]):
            # Native minified exports omit fill IDs; retain explicit array indices.
            key = (pair, number, order_index)
            assert key not in seen
            seen.add(key)
            when = int(order["order_filled_timestamp"])
            amount = D(str(order["amount"]))
            assert amount.is_finite() and amount > 0
            if when < slot:
                quantity += amount if order["ft_is_entry"] else -amount
            if when == slot:
                boundaries.append(
                    {"trade_index": number, "archive_order_index": [number, order_index]}
                )
            distances.append((abs(when - slot), when, order_index))
        row = {
            "trade_index": number,
            "is_short": trade["is_short"],
            "serialized_quantity_before_slot": str(quantity),
            "open": opened,
            "close": closed,
        }
        if opened < slot < closed:
            assert quantity > 0
            held.append(row)
        elif quantity != 0:
            residuals.append(row)
        if opened <= slot + 4 * 3600000 and closed >= slot - 4 * 3600000:
            bracket.append(row)
    distances.sort()
    return {
        "pair": pair,
        "slot_UTC": datetime.fromtimestamp(slot / 1000, UTC).isoformat(),
        "active_native_campaigns": held,
        "boundary_fills": boundaries,
        "overlapping_00_to_08UTC_campaigns": bracket,
        "gross_active_serialized_quantity": str(
            sum((D(v["serialized_quantity_before_slot"]) for v in held), D(0))
        ),
        "closed_campaign_serialization_residuals": residuals,
        "nearest_fill_hours": distances[0][0] / 3600000 if distances else None,
        "source_rate_unknown": True,
        "no_modeled_exposure_in_entire_bracket": not bracket and not boundaries,
        "scope": "Native archived model campaign state, not exchange position/settlement evidence",
    }


def main():
    hashes, accounts, sources, curve_checks = {}, [], [], []
    owner = ROOT / "research/crypto/strategy_comparison_wide_20261105"
    groups = [
        "wide_strategy_comparison_20261105_v001",
        "wide_strategy_cost15_20261105_v001",
        "wide_strategy_x7_20261105_v001",
    ]
    common_pairs = None
    for group in groups:
        base = ROOT / "user_data/crypto_quant" / group
        plan_path = base / "registered_plan.json"
        plan = read(plan_path)
        hashes[str(plan_path)] = sha(plan_path)
        assert (base / "completion.json").exists()
        hashes[str(base / "completion.json")] = sha(base / "completion.json")
        assert plan["initial_equity_usdt"] == 1000 and len(plan["pairs"]) == 40
        if common_pairs is None:
            common_pairs = plan["pairs"]
        assert plan["pairs"] == common_pairs
        for result_path in sorted(base.glob("*/result.json")):
            folder = result_path.parent
            result = read(result_path)
            archive = next(folder.glob("native_results/*.zip"))
            native = stats_from(archive)
            config_path = folder / "config.json"
            config = read(config_path)
            assert config["dry_run"] and config["dry_run_wallet"] == 1000
            assert config["max_open_trades"] == 6 and config["trading_mode"] == "futures"
            assert config["margin_mode"] == "isolated" and config["timeframe"] == "5m"
            assert config["exchange"]["pair_whitelist"] == common_pairs
            assert config["fee"] == result["cost_bps_per_side"] / 10000
            assert all(t["leverage"] == 1 for t in native["trades"])
            hashes[str(config_path)] = sha(config_path)
            for path in [result_path, archive, folder / "hourly_equity.parquet"]:
                hashes[str(path)] = sha(path)
            curve = pd.read_parquet(folder / "hourly_equity.parquet").set_index("date")
            equity = curve["hourly_mark_equity"]
            expected = pd.date_range("2025-01-01", "2026-08-31T23:00:00", freq="h", tz="UTC")
            assert equity.index.equals(expected) and len(equity) == 14592
            assert np.isfinite(equity).all() and equity.iloc[0] == 1000
            full = next(v for v in result["periods"] if v["name"] == "continuous")
            ret = float(equity.iloc[-1] / equity.iloc[0] - 1)
            dd = float((1 - equity / equity.cummax()).max())
            assert abs(ret - full["net_return"]) < 1e-10
            assert abs(dd - full["hourly_mark_drawdown"]) < 1e-10
            assert native["total_trades"] == result["native_terminal_trade_count"]
            candidate = next(c for c in plan["candidates"] if c["name"] == result["candidate"])
            for filename, record in candidate["source_files"].items():
                assert (
                    sha(record["path"]) == sha(folder / "strategies" / filename) == record["sha256"]
                )
                hashes[record["path"]] = record["sha256"]
                source = Path(record["path"]).read_text().splitlines()
                sources.append(
                    {
                        "account": folder.name,
                        "path": record["path"],
                        "funding_text_lines": [
                            {"line": i + 1, "text": s.strip()}
                            for i, s in enumerate(source)
                            if "funding" in s.lower()
                        ],
                        "scope": "Text inventory, not formal absence-of-indirect-dependency proof",
                    }
                )
            gaps = [exposure(native["trades"], pair) for pair in PAIRS]
            assert all(g["no_modeled_exposure_in_entire_bracket"] for g in gaps)
            accounts.append(
                {
                    "account": folder.name,
                    "cohort": "NFI40_1000USDT_1x_6slots",
                    "cost_bps_per_side": result["cost_bps_per_side"],
                    "gaps": gaps,
                    "old_model_net_return": ret,
                    "old_model_hourly_mark_drawdown": dd,
                    "native_campaigns": native["total_trades"],
                    "hourly_points": len(equity),
                    "new_account": False,
                    "complete_authenticated_settlement": False,
                    "source_result": str(result_path),
                }
            )
            curve_checks.append({"account": folder.name, "return": ret, "drawdown": dd})
    assert len(accounts) == 6
    comparator = ROOT / "research/crypto/skew_vol_native_20261113_v1/v002/comparison.json"
    parent = read(comparator)
    hashes[str(comparator)] = sha(comparator)
    for row in parent["evidence"]:
        archive = Path(row["archive"]["path"])
        assert sha(archive) == row["archive"]["sha256"]
        hashes[str(archive)] = sha(archive)
        native = stats_from(archive)
        gaps = [exposure(native["trades"], pair) for pair in PAIRS]
        assert all(g["active_native_campaigns"] for g in gaps)
        accounts.append(
            {
                "account": row["account"],
                "cohort": "skew_lowvol_40pairs_20slots",
                "gaps": gaps,
                "new_account": False,
                "complete_actual_settlement_economics": "UNKNOWN",
            }
        )
    plan = read(owner / "plan.json")
    receipts = plan["data_receipts"]
    assert sha(receipts["path"]) == receipts["sha256"]
    for relative, digest in read(receipts["path"]).items():
        path = Path(plan["datadir"]) / relative
        assert sha(path) == digest
        hashes[str(path)] = digest
    assert sha(plan["engine_manifest"]["path"]) == plan["engine_manifest"]["sha256"]
    manifest = read(plan["engine_manifest"]["path"])
    assert manifest["commit"] == plan["engine_commit"]
    for relative, digest in manifest["files"].items():
        assert sha(Path(plan["engine_source"]) / relative) == digest

    def save(name, value):
        (HERE / name).write_text(json.dumps(value, indent=2) + "\n")

    save("input_hashes.json", hashes)
    save(
        "account_exposure_audit.json",
        {
            "passed": True,
            "accounts": accounts,
            "native_account_archives_reused": 14,
            "new_financial_accounts": 0,
            "nfi_gap_exposures_absent": 12,
            "skew_lowvol_gap_exposures_present": 16,
            "native_source_files_verified": len(manifest["files"]),
            "funding_rates_imputed": False,
            "source_text_inventory": sources,
            "nfi_curve_checks": curve_checks,
            "orders_enabled": False,
            "production_eligible": False,
            "goal_complete": False,
        },
    )
    print(
        "14native archives:12NFI slot exposures absent;"
        "16skew/lowvol exposures present;6full curves verified"
    )


if __name__ == "__main__":
    main()
