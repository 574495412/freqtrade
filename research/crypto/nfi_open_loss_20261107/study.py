"""Freeze the open-loss risk overlay and check it through native replay."""

import math
import shutil
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from research.crypto.nfi_btc_leverage_20261107 import evaluate as marks
from research.crypto.nfi_btc_leverage_20261107 import study as leverage
from research.crypto.nfi_solo_allocation_20261107.run import jsonl
from research.crypto.nfi_strength_allocation_20261107 import temporal
from research.crypto.nfi_strength_allocation_20261107.audit import native_trades, read
from research.crypto.nfi_strength_allocation_20261107.register import DATA, PARENT, record
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked, save, sha


HERE = Path(__file__).resolve().parent
OUT = DATA / "nfi_open_loss_20261107_v001"
POLICY = HERE / "registration.json"
RESULT = HERE / "result.json"
NAME = "OpenLoss"
COSTS = (15, 8)
REASON = "same_side_open_loss"


def register():
    if POLICY.exists() or OUT.exists():
        raise FileExistsError("Study is already registered")
    source = leverage.check()["2024_Native2x"]
    parent = read(source)
    old = parent["candidates"][0]
    cfg = HERE / f"{NAME}.json"
    plan = HERE / "native_2024.json"
    save(cfg, {**read(checked(old["config"])), "strategy": NAME})
    new = {
        **old,
        "strategy": NAME,
        "name": NAME,
        "config": record(cfg),
        "source_files": {**old["source_files"], "OpenLoss.py": record(HERE / "OpenLoss.py")},
        "source_and_risk_behavior": "Native 2x with registered same-side open-loss exit.",
    }
    save(
        plan,
        {
            **parent,
            "schema": "freqtrade.research.nfi-open-loss.v1",
            "registered_at": datetime.now(UTC).isoformat(),
            "candidates": [new],
        },
    )
    save(
        POLICY,
        {
            "registered_at": datetime.now(UTC).isoformat(),
            "plan": record(plan),
            "sources": [
                record(path)
                for path in (
                    Path(__file__),
                    source,
                    leverage.PROTOCOL,
                    leverage.RESULT,
                    Path(marks.__file__),
                    Path(temporal.__file__),
                    leverage.a13.OUT / "5m/admission.json",
                )
            ],
            "known_history": (
                "The incumbent 1x and 2x 2024 accounts and their drawdown were known. "
                "This candidate's outcomes are unknown. Same-calendar adaptation only."
            ),
            "rule": (
                "Snapshot the inventory before each native 5m loop. Consider each side "
                "with at least three filled open positions and no pending orders among them. "
                "Value signed open price PnL at the exact preceding candle close versus "
                "native remaining entry basis (stake times leverage). If the side loses "
                "5% of remaining entry notional, select the most negative dollar contributor, "
                "ties by pair. No triggers on missing bars or if leverage differs. Suppress "
                "positive adjustment of the selected trade for this loop and request a "
                "native full exit if the incumbent has no custom exit and no order is "
                "pending. Other adjustments, native confirmation, pricing and entries unchanged. "
                "The trigger excludes fees and funding; economics include both. Re-entry "
                "remains native. This is a research exit signal, not an exchange stop."
            ),
            "gate": (
                "Both 2024 costs require <=25% observed 5m drawdown and lower than full 2x, "
                ">=60% of 2x excess over 1x retained, return/drawdown >= both controls, "
                "net return > 1x in each half, >=60 naturally completed trades, "
                "at least 3 filled overlay exits across at least 2 UTC dates, "
                "no model liquidation and no unknown trigger inputs. "
                "Passing permits separately registered temporal testing."
            ),
            "orders_enabled": False,
        },
    )


def inventory(trade, timestamp):
    qty = basis = Decimal(0)
    for order in trade["orders"]:
        if order["order_filled_timestamp"] >= timestamp:
            continue
        size = order["amount"]
        if order["ft_is_entry"]:
            qty += size
            basis += size * order["safe_price"]
        else:
            basis -= basis / qty * size
            qty -= size
    return float(qty), float(basis)


def date_key(trade):
    return trade["pair"], trade["open_timestamp"], bool(trade["is_short"])


def audit(plan, folder, trades):
    archive = {date_key(trade): trade for trade in trades}
    receipts = read(checked(plan["data_receipts"]))
    closes = {}
    trigger_path = folder / "open_loss_trigger.jsonl"
    rows = jsonl(trigger_path) if trigger_path.exists() else []
    seen = {}
    by_day = Counter()
    for row in rows:
        at = pd.Timestamp(row["time"])
        clock = at.value // 1_000_000
        short = row["side"] == "short"
        prior = at - pd.Timedelta(minutes=5)
        if row["prior_bar"] != prior.isoformat() or len(row["holdings"]) < 3:
            raise ValueError("Invalid signal clock or breadth")
        observed = {(v["pair"], v["open_ms"], short) for v in row["holdings"]}
        expected = {
            key
            for key, trade in archive.items()
            if key[2] == short
            and key[1] < clock <= trade["close_timestamp"]
            and inventory(trade, clock)[0] > 1e-12
        }
        if observed != expected:
            raise ValueError(f"Trace does not match open inventory at {at}")
        scored = []
        for v in row["holdings"]:
            pair = v["pair"]
            if pair not in closes:
                slug = pair.replace("/", "_").replace(":", "_")
                rel = f"futures/{slug}-5m-futures.feather"
                path = checked({"path": str(Path(plan["datadir"]) / rel), "sha256": receipts[rel]})
                df = pd.read_feather(path, columns=["date", "close"])
                closes[pair] = df.set_index("date").close
            rate = closes[pair].at[prior]
            qty, basis = inventory(archive[(pair, v["open_ms"], short)], clock)
            pnl = (qty * rate - basis) * (-1 if short else 1)
            if not (
                math.isclose(rate, v["prior_close"], rel_tol=1e-12)
                and math.isclose(qty, v["quantity"], rel_tol=1e-9, abs_tol=1e-8)
                and math.isclose(basis, v["basis"], rel_tol=1e-9, abs_tol=1e-6)
                and math.isclose(pnl, v["price_pnl"], rel_tol=1e-8, abs_tol=1e-6)
            ):
                raise ValueError(f"Pre-bar loss does not reconcile: {pair} {at}")
            scored.append((pnl, pair, v["open_ms"], basis))
        loss = -math.fsum(r[0] for r in scored) / math.fsum(r[3] for r in scored)
        worst = min(scored)
        if (
            not math.isclose(loss, row["loss_ratio"], rel_tol=1e-8, abs_tol=1e-8)
            or loss < 0.05 - 1e-8
            or (row["target"], row["target_open_ms"]) != worst[1:3]
        ):
            raise ValueError("Target differs from independently scored group")
        seen[(row["target"], row["target_open_ms"], short, clock)] = row
        by_day[str(at.date())] += 1
    exits = filled_exits(trades, seen)
    unknown = folder / "open_loss_unknown.jsonl"
    return {
        "trigger_count": len(rows),
        "trigger_dates": dict(by_day),
        "trigger_trace": record(trigger_path) if rows else None,
        "overlay_exits": exits,
        "exit_dates": len({e["time"][:10] for e in exits}),
        "unknown_inputs": len(jsonl(unknown)) if unknown.exists() else 0,
    }


def filled_exits(trades, seen):
    exits = []
    for t in trades:
        if t["exit_reason"] != REASON:
            continue
        exit_order = t["orders"][-1]
        fill_time = exit_order["order_filled_timestamp"]
        key = (*date_key(t), fill_time)
        if key not in seen or exit_order["ft_is_entry"]:
            raise ValueError(f"Native overlay exit lacks matching prior-bar trigger: {key}")
        if any(o["ft_is_entry"] and o["order_filled_timestamp"] == fill_time for o in t["orders"]):
            raise ValueError("Selected position added margin before the overlay exit")
        exits.append(
            {
                "pair": t["pair"],
                "time": seen[key]["time"],
                "group_open_loss": seen[key]["loss_ratio"],
                "trade_pnl": float(t["profit_abs"]),
            }
        )
    return exits


def account(plan_path, cost):
    plan = read(plan_path)
    (case,) = plan["candidates"]
    key = f"2024_{NAME}_{cost}bps"
    folder = OUT / key
    log_path = folder.with_suffix(".log")
    with log_path.open("x") as log:
        subprocess.run(
            [
                sys.executable,
                "-B",
                str(PARENT / "run_wide_comparison.py"),
                "--plan",
                str(plan_path),
                "--candidate",
                NAME,
                "--cost-bps",
                str(cost),
                "--output",
                str(folder),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    if "Unexpected error" in log_path.read_text():
        raise ValueError("Native strategy callback exception")
    summary = temporal.audit_one(plan, case, cost, folder)
    trades, _ = native_trades(plan, folder, NAME, cost)
    summary.update(audit(plan, folder, trades))
    summary["native_liquidations"] = sum(t["exit_reason"] == "liquidation" for t in trades)
    save(folder / "audit.json", summary)
    cache = folder / "lossless_cache"
    if cache.is_dir() and not cache.is_symlink():
        shutil.rmtree(cache)
    print(
        key,
        summary["periods"]["continuous"]["net_return"],
        len(summary["overlay_exits"]),
        flush=True,
    )
    return key, summary


def run():
    p = read(POLICY)
    for ref in p["sources"]:
        checked(ref)
    path = checked(p["plan"])
    OUT.mkdir()
    accounts = dict(account(path, cost) for cost in COSTS)
    marks.study.OUT = OUT
    marked, sources = marks.value(read(path), "2024", accounts)
    baseline = read(leverage.RESULT)["stages"]["2024"]["comparison"]
    comparisons = {}
    for cost in COSTS:
        key = f"2024_{NAME}_{cost}bps"
        base, uniform = (baseline[str(cost)][name] for name in ("Original1x", "Native2x"))
        account_row = accounts[key]
        p = account_row["periods"]
        net = p["continuous"]["net_return"]
        dd = marked[key]["drawdown"]
        retained = (net - base["return"]) / (uniform["return"] - base["return"])
        comparisons[str(cost)] = {
            "original": base,
            "uniform": uniform,
            "net": net,
            "drawdown": dd,
            "retention": retained,
            "checks": {
                "risk": dd <= 0.25 and dd < uniform["drawdown"],
                "return_retention": retained >= 0.6,
                "risk_adjusted": all(
                    net * r["drawdown"] >= r["return"] * dd for r in (base, uniform)
                ),
                "halves": all(p[h]["net_return"] > base["period_returns"][h] for h in ("H1", "H2")),
                "sample": p["continuous"]["completed_campaigns"] >= 60
                and len(account_row["overlay_exits"]) >= 3
                and account_row["exit_dates"] >= 2,
                "no_liquidations": account_row["native_liquidations"] == 0,
                "no_unknown": account_row["unknown_inputs"] == 0,
            },
        }
    save(
        RESULT,
        {
            "registration_sha256": sha(POLICY),
            "accounts": accounts,
            "marks": marked,
            "mark_sources": sources,
            "comparisons": comparisons,
            "advance": all(all(row["checks"].values()) for row in comparisons.values()),
            "paper_eligible": False,
        },
    )
    print(comparisons, flush=True)


if __name__ == "__main__":
    {"register": register, "run": run}[sys.argv[1]]()
