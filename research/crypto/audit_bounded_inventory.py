"""Reconstruct whole campaigns and original stop budgets from actual native fills."""

import argparse
import math
from collections import Counter
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.audit_contract_screen import audit
from research.crypto.audit_ensemble import raw_trades
from research.crypto.autonomous_study import sleeve_summary


def wilder_atr(frame):
    """Independent scalar Wilder14 seed/recurrence; no strategy or TA-Lib imports."""
    values = frame[["high", "low", "close"]].to_numpy(dtype=float)
    tr, result = np.full(len(frame), np.nan), np.full(len(frame), np.nan)
    for i in range(1, len(frame)):
        high, low = values[i, :2]
        previous = values[i - 1, 2]
        tr[i] = max(high - low, abs(high - previous), abs(low - previous))
        if i == 14:
            result[i] = math.fsum(tr[1:15]) / 14
        elif i > 14:
            result[i] = (13 * result[i - 1] + tr[i]) / 14
    return pd.Series(result, index=pd.DatetimeIndex(frame.date))


def campaign_audit(folder, name):  # noqa: C901 - sequential independent campaign ledger checks
    plan, result = read(folder / "plan.json"), read(folder / "result.json")
    trades = raw_trades(folder, name)
    report = audit(folder, name)
    rank = {
        symbol.removesuffix("USDT") + "/USDT:USDT": i for i, symbol in enumerate(plan["symbols"])
    }
    prices, atrs = {}, {}
    start = pd.Timestamp(result["start"], tz="UTC")
    for pair in {t["pair"] for t in trades}:
        stem = pair.replace("/", "_").replace(":", "_")
        root = Path(plan["dataset"]) / "candles/futures"
        prices[pair] = pd.read_feather(root / f"{stem}-5m-futures.feather").set_index("date")
        for tf, hours in [("1h", 1), ("4h", 4)]:
            f = pd.read_feather(root / f"{stem}-{tf}-futures.feather")
            f = f.loc[f.date.ge(start - pd.Timedelta(hours=800 * hours))]
            atrs[pair, tf] = wilder_atr(f)
    ratios, plan_ratios, margin_ratios = [], [], []
    errors = []
    for index, t in enumerate(trades):
        entry = [o for o in t["orders"] if o["ft_is_entry"]]
        exits = [o for o in t["orders"] if not o["ft_is_entry"]]
        maximum = 1 if name == "InventorySingle" else 3
        if not 1 <= len(entry) <= maximum or len(exits) != 1:
            raise ValueError("Campaign entry/exit count changed")
        if entry[0]["order_filled_timestamp"] != t["open_timestamp"]:
            raise ValueError("Immediate-first-fill capital replay assumption failed")
        clock, pair = t["open_timestamp"], t["pair"]
        # Native handles all existing pairs before new ones. For simultaneous new
        # same-bar round trips, the fixed whitelist order determines realized capital.
        prior = [
            x["profit_abs"]
            for x in trades
            if x["close_timestamp"] < clock
            or (
                x["close_timestamp"] == clock
                and (
                    x["open_timestamp"] < clock
                    or (x["open_timestamp"] == clock and rank[x["pair"]] < rank[pair])
                )
            )
        ]
        capital = (plan["initial_equity_usdt"] + math.fsum(prior)) * 0.99
        small = t["enter_tag"] == "small_inventory"
        risk, margin = capital * (0.001 if small else 0.005), capital * (0.02 if small else 0.20)
        opened = pd.to_datetime(clock, unit="ms", utc=True)
        tf = "1h" if small else "4h"
        atr_clock = opened.floor(tf) - pd.Timedelta(hours=1 if small else 4)
        atr = float(atrs[pair, tf].loc[atr_clock])
        first = Decimal(str(entry[0]["safe_price"]))
        sign = Decimal(-1 if t["is_short"] else 1)
        distance = max(0.03 if small else 0.08, 4 * atr / float(first))
        stop = first * (1 - sign * Decimal(str(distance)))
        if distance > (0.10 if small else 0.30) + 1e-10:
            raise ValueError("Campaign entered outside volatility ceiling")
        final_stop = Decimal(str(t["stop_loss_abs"]))
        if sign * (final_stop - stop) < -abs(first) * Decimal("1e-9"):
            raise ValueError("Native final stop widened beyond the original stop")
        fee, leverage = Decimal(str(t["fee_open"])), Decimal(str(t["leverage"]))
        first_margin = Decimal(str(entry[0]["amount"])) * first / leverage
        planned_loss = Decimal(0)
        for n in range(maximum):
            p = first * (1 - sign * Decimal(str(distance)) * Decimal(n) / 4)
            planned_loss += (
                first_margin * leverage * (-sign * (stop / p - 1) + fee * (1 + stop / p))
            )
        plan_ratios.append(float(planned_loss) / risk)
        loss, used = Decimal(0), Decimal(0)
        for n, order in enumerate(entry):
            p, q = Decimal(str(order["safe_price"])), Decimal(str(order["amount"]))
            stake = q * p / leverage
            used += stake
            loss += q * (-sign * (stop - p) + fee * (p + stop))
            if n:
                level = first * (1 - sign * Decimal(str(distance)) * Decimal(n) / 4)
                when = pd.to_datetime(order["order_filled_timestamp"], unit="ms", utc=True)
                closed = float(prices[pair].loc[when - pd.Timedelta(minutes=5), "close"])
                if sign * (p - level) > abs(first) * Decimal("1e-9"):
                    raise ValueError("Added before executable grid level")
                if float(sign) * (closed - float(level)) > float(first) * 1e-9:
                    raise ValueError("Added without a completed-bar level crossing")
                if stake > first_margin + Decimal("0.00001"):
                    raise ValueError("A later tranche exceeded the first stake")
                if order["ft_order_tag"] != f"equal_tranche_{n + 1}":
                    raise ValueError("Unexpected addition tag")
            ratios.append(float(loss) / risk)
            margin_ratios.append(float(used) / margin)
            if float(loss) > risk + 1e-5 or float(used) > margin + 1e-5:
                errors.append(
                    {
                        "trade_index": index,
                        "risk": risk,
                        "loss": float(loss),
                        "margin_cap": margin,
                        "used": float(used),
                    }
                )
        if float(planned_loss) > risk + 1e-5:
            errors.append({"trade_index": index, "planned_loss": float(planned_loss), "risk": risk})
    report.update(
        sleeves={
            tag: sleeve_summary([t for t in trades if t["enter_tag"] == tag])
            for tag in ["small_inventory", "core_inventory"]
        },
        entry_count_distribution=dict(
            Counter(len([o for o in t["orders"] if o["ft_is_entry"]]) for t in trades)
        ),
        maximum_planned_stop_loss_over_initial_budget=max(plan_ratios, default=0),
        maximum_actual_entry_prefix_stop_loss_over_initial_budget=max(ratios, default=0),
        maximum_campaign_margin_over_initial_cap=max(margin_ratios, default=0),
        campaign_risk_audit_passed=not errors,
        risk_errors=errors[:20],
        risk_audit_source_sha256=sha(Path(__file__)),
        risk_scope=(
            "Fixed original stop and known fees; excludes gap loss and subsequent funding. "
            "Final stop and frozen callback checked, not a full stop-order event log."
        ),
        capital_replay=(
            "99percent realized capital; native existing-pairs-first and whitelist-order "
            "same-bar roundtrip handling; immediate first fills checked."
        ),
    )
    save(folder / "campaign_audit.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for name in ["InventorySingle", "InventoryGrid3", "InventorySmallGrid3", "InventoryCoreGrid3"]:
        folder = args.run / "train" / name / "cost_3bps"
        a = campaign_audit(folder, name)
        rows.append({"result": read(folder / "result.json"), "campaign_audit": a})
        print(
            {
                "candidate": name,
                "risk_audit_passed": a["campaign_risk_audit_passed"],
                "entries": a["entry_count_distribution"],
                "errors": a["risk_errors"][:1],
            }
        )
    save(
        args.run / "summary.json",
        {
            "accounts": rows,
            "native_accounts": 4,
            "all_campaign_risk_audits_passed": all(
                r["campaign_audit"]["campaign_risk_audit_passed"] for r in rows
            ),
            "qualified_candidates": [],
            "production_eligible": False,
            "orders_enabled": False,
        },
    )
