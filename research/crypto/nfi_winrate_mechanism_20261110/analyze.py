"""Read-only diagnostics of completed native accounts; no alternative execution engine."""

import ast
import hashlib
import json
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
import talib


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PARENT = ROOT / "research/crypto/nfi_truth_audit_20261109"
OUT = ROOT / "user_data/crypto_quant/nfi_winrate_mechanism_20261110"
SOURCE = (
    ROOT
    / "research/crypto/strategy_comparison_20261105/sources/nfi-x8-1x/NostalgiaForInfinityX8.py"
)


def ref(path):
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def checked(record):
    path = Path(record["path"])
    if ref(path)["sha256"] != record["sha256"]:
        raise ValueError("Native evidence changed")
    return path


def native_trades(archive):
    with ZipFile(archive) as z:
        for name in z.namelist():
            if name.endswith(".json") and "_config" not in name and "_market_change" not in name:
                obj = json.loads(z.read(name))
                if "strategy" in obj:
                    return obj["strategy"]["FlatTopBoost"]["trades"]
    raise ValueError("Missing actual native financial archive")


def fixed_horizon(rows, plan):
    """Gross price-direction labels on native first fills, not hypothetical trades or returns."""
    result = {}
    receipts = json.loads(checked(plan["data_receipts"]).read_text())
    verified = set()
    is_spot = plan["allowed_leverage"] == 1
    directory = Path(plan["datadir"]) / ("" if is_spot else "futures")
    for hours in (1, 4, 24):
        wins = ties = missing = n = 0
        for pair, group in rows.groupby("pair"):
            stem = pair.replace("/", "_").replace(":", "_")
            path = directory / (stem + ("-5m.feather" if is_spot else "-5m-futures.feather"))
            if path not in verified:
                name = str(path.relative_to(Path(plan["datadir"])))
                if ref(path)["sha256"] != receipts[name]:
                    raise ValueError("Frozen price-label input changed")
                verified.add(path)
            prices = pd.read_feather(path, columns=["date", "open"]).set_index("date").open
            for t in group.itertuples():
                when = pd.Timestamp(t.open_date) + pd.Timedelta(hours=hours)
                if when > pd.Timestamp(plan["comparison_end"]) or when not in prices.index:
                    missing += 1
                    continue
                move = (-1 if t.is_short else 1) * (prices.at[when] / t.first_fill_price - 1)
                n += 1
                wins += int(move > 0)
                ties += int(move == 0)
        result[str(hours) + "h"] = {
            "observed": n,
            "missing_or_beyond_cutoff": missing,
            "positive_gross_price_moves": wins,
            "ties": ties,
            "positive_price_direction_pct": wins / n * 100 if n else None,
        }
    return result


def one_account(item, plan):
    archive = checked(item["native_archive"])
    records = native_trades(archive)
    rows = []
    cutoff = pd.Timestamp(plan["comparison_end"]).timestamp() * 1000
    for i, t in enumerate(records):
        entries = [o for o in t["orders"] if o["ft_is_entry"]]
        first = entries[0]["safe_price"]
        turnover = sum(o["amount"] * o["safe_price"] for o in t["orders"])
        adverse = 1 - t["max_rate"] / first if t["is_short"] else t["min_rate"] / first - 1
        rows.append(
            {
                "native_campaign_index": i,
                "pair": t["pair"],
                "is_short": t["is_short"],
                "open_date": t["open_date"],
                "close_date": t["close_date"],
                "natural": t["exit_reason"] != "force_exit" and t["close_timestamp"] < cutoff,
                "net_pnl_USDT": t["profit_abs"],
                "duration_hours": t["trade_duration"] / 60,
                "filled_entries": len(entries),
                "first_fill_price": first,
                "all_fill_turnover_USDT": turnover,
                "enter_tag": t["enter_tag"],
                "exit_reason": t["exit_reason"],
                "native_price_extreme_vs_first_fill_pct": adverse * 100,
                "extra_bps_to_zero_profit_on_fixed_fills": t["profit_abs"] / turnover * 10000,
                "adverse_direction_adds_vs_first_fill": sum(
                    o["safe_price"] > first if t["is_short"] else o["safe_price"] < first
                    for o in entries[1:]
                ),
            }
        )
    f = pd.DataFrame(rows)
    natural = f[f.natural]
    winners = natural[natural.net_pnl_USDT > 0]
    losses = natural[natural.net_pnl_USDT < 0]
    terminal = f[~f.natural]
    no_add = natural[natural.filled_entries == 1]
    curve = pd.read_parquet(checked(item["curve"]))
    avgwin = float(winners.net_pnl_USDT.mean())
    avgloss = float(-losses.net_pnl_USDT.mean()) if len(losses) else None
    open_pnl = float(
        curve.marked_equity.iloc[-1]
        - curve.marked_equity.iloc[0]
        - curve.closed_realized_pnl.iloc[-1]
    )
    closed_pnl = float(natural.net_pnl_USDT.sum())
    if (
        abs(closed_pnl + open_pnl - (curve.marked_equity.iloc[-1] - curve.marked_equity.iloc[0]))
        > 1e-6
    ):
        raise ValueError("Native natural PnL + cutoff mark PnL do not reconcile")
    if len(winners) != item["natural_wins"] or len(natural) != item["natural_completed_campaigns"]:
        raise ValueError("Native campaign denominator mismatch")
    by_time = natural.groupby("open_date").size()
    result = {
        "name": item["name"],
        "native_archive": item["native_archive"],
        "natural_completed": len(natural),
        "natural_winners": len(winners),
        "natural_losers": len(losses),
        "natural_win_rate_pct": len(winners) / len(natural) * 100,
        "all_terminal_win_rate_pct": float((f.net_pnl_USDT > 0).mean() * 100),
        "average_natural_win_USDT": avgwin,
        "average_natural_loss_abs_USDT": avgloss,
        "one_average_loss_in_average_wins": avgloss / avgwin if avgloss else None,
        "median_natural_win_hold_hours": float(winners.duration_hours.median()),
        "p95_natural_win_hold_hours": float(winners.duration_hours.quantile(0.95)),
        "max_natural_win_hold_hours": float(winners.duration_hours.max()),
        "average_natural_loss_hold_days": float(losses.duration_hours.mean() / 24)
        if len(losses)
        else None,
        "winner_cycles_without_additions": int((winners.filled_entries == 1).sum()),
        "winner_cycles_with_additions": int((winners.filled_entries > 1).sum()),
        "no_add_natural_campaigns": len(no_add),
        "no_add_natural_win_rate_pct": float((no_add.net_pnl_USDT > 0).mean() * 100),
        "all_addition_fills": int((f.filled_entries - 1).sum()),
        "adverse_direction_adds_vs_first_fill": int(f.adverse_direction_adds_vs_first_fill.sum()),
        "max_filled_entries_in_one_campaign": int(f.filled_entries.max()),
        "natural_winners_price_adverse5pct": int(
            (winners.native_price_extreme_vs_first_fill_pct <= -5).sum()
        ),
        "natural_winners_price_adverse10pct": int(
            (winners.native_price_extreme_vs_first_fill_pct <= -10).sum()
        ),
        "natural_winners_price_adverse20pct": int(
            (winners.native_price_extreme_vs_first_fill_pct <= -20).sum()
        ),
        "natural_winners_under0_1USDT": int((winners.net_pnl_USDT < 0.1).sum()),
        "top10_winners_share_of_gross_wins": float(
            winners.net_pnl_USDT.nlargest(10).sum() / winners.net_pnl_USDT.sum()
        ),
        "fixed_fills_winners_eliminated_by_extra7bps_each_side": int(
            (winners.extra_bps_to_zero_profit_on_fixed_fills <= 7).sum()
        ),
        "natural_closed_pnl_USDT": closed_pnl,
        "cutoff_open_mark_pnl_USDT": open_pnl,
        "open_loss_in_average_wins": -open_pnl / avgwin if open_pnl < 0 else 0,
        "short_campaigns": int(f.is_short.sum()),
        "all_campaigns": len(f),
        "distinct_natural_entry_timestamps": len(by_time),
        "natural_cycles_sharing_entry_timestamps": int(by_time[by_time > 1].sum()),
        "natural_loss_cases": losses.to_dict("records"),
        "terminal_force_exit_cases": terminal.to_dict("records"),
        "entry_tag_counts": dict(Counter(f.enter_tag)),
        "fixed_horizon_diagnostic": fixed_horizon(f, plan),
    }
    f.to_csv(OUT / (item["name"] + "-native-campaign-diagnostics.csv"), index=False)
    return result


def terminal_snapshot(item, plan):
    """Rebuild last closed-day risk inputs with the source's exact indicator formulas."""
    trades = native_trades(checked(item["native_archive"]))
    receipts = json.loads(checked(plan["data_receipts"]).read_text())
    directory = Path(plan["datadir"]) / "futures"
    cutoff = pd.Timestamp(plan["comparison_end"])
    effective = json.loads(
        (Path(item["native_result"]["path"]).parent / "effective_config.json").read_text()
    )
    startup = int(effective["startup_candle_count"])
    warmup_start = pd.Timestamp(plan["start"]) - pd.Timedelta(days=startup)
    rows = []
    for trade in trades:
        if trade["exit_reason"] != "force_exit":
            continue
        stem = trade["pair"].replace("/", "_").replace(":", "_")
        daily_path = directory / (stem + "-1d-futures.feather")
        price_path = directory / (stem + "-5m-futures.feather")
        for path in (daily_path, price_path):
            key = str(path.relative_to(Path(plan["datadir"])))
            if ref(path)["sha256"] != receipts[key]:
                raise ValueError("Risk snapshot input changed")
        daily = pd.read_feather(daily_path)
        daily = daily.loc[(daily.date >= warmup_start) & (daily.date < cutoff.floor("D"))].copy()
        close = daily.close.to_numpy()
        ema_gap = (talib.EMA(close, 50)[-1] / talib.EMA(close, 200)[-1] - 1) * 100
        if trade["is_short"]:
            ema_gap = -ema_gap
        daily_range = talib.SMA(((daily.high - daily.low) / daily.low * 100).to_numpy(), 14)[-1]
        prices = pd.read_feather(price_path).set_index("date").open
        current = float(prices.at[cutoff])
        first = next(o["safe_price"] for o in trade["orders"] if o["ft_is_entry"])
        adverse = (1 if trade["is_short"] else -1) * (current / first - 1) * 100
        age = (cutoff - pd.Timestamp(trade["open_date"])).total_seconds() / 86400
        rows.append(
            {
                "pair": trade["pair"],
                "last_closed_day": str(daily.date.iloc[-1]),
                "informative_startup_candles": startup,
                "daily_warmup_start": str(warmup_start),
                "age_days": age,
                "adverse_price_move_at_cutoff_pct": adverse,
                "signed_ema50_200_gap_pct": float(ema_gap),
                "daily_range14_pct": float(daily_range),
                "structure_exit_two_conditions": bool(ema_gap <= -35 and adverse >= 25),
                "stale_exit_three_conditions": bool(
                    age >= 14 and adverse >= 10 and daily_range < 7
                ),
                "daily_input": ref(daily_path),
                "price_input": ref(price_path),
            }
        )
    return {
        "role": "Auxiliary risk-input snapshot, not full custom-exit control-flow replay",
        "rows": rows,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    summary = json.loads((PARENT / "cap100-comparison.json").read_text())
    plans = {
        item["name"]: json.loads(
            (Path(item["native_result"]["path"]).parent / "registered_plan.json").read_text()
        )
        for item in summary["accounts"]
    }
    if ref(SOURCE)["sha256"] != "fc5cb221f29ebca5f336429d97920eb93917cdd87f9769381b23b467ca6d5bf3":
        raise ValueError("Executed NFI source changed")
    module = ast.parse(SOURCE.read_text())
    cls = next(
        n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "NostalgiaForInfinityX8"
    )
    names = {
        "stoploss",
        "use_custom_stoploss",
        "stops_enable",
        "doom_stops_enable",
        "system_v4_stops_enable",
        "system_v4_bad_trade_exit_enable",
        "legacy_system_bad_trade_exit_enable",
        "bad_trade_controller_stale_min_age_days",
        "bad_trade_controller_stale_max_daily_range_pct",
        "bad_trade_controller_stale_min_adverse_move_pct",
        "bad_trade_controller_structure_break_ema_gap_pct",
        "bad_trade_controller_structure_break_min_adverse_move_pct",
    }
    constants = {
        n.targets[0].id: ast.literal_eval(n.value)
        for n in cls.body
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id in names
    }
    output = {
        "role": "Native artifact diagnostics; no account simulation or causal ablation",
        "inputs": [ref(PARENT / "cap100-comparison.json"), ref(SOURCE)],
        "source_risk_constants": constants,
        "accounts": [one_account(item, plans[item["name"]]) for item in summary["accounts"]],
        "limits": [
            "No ablations: causal contribution sizes remain unidentified",
            "Price extremes versus first fill are asset moves, not position/account MAE",
            "Fixed horizon labels ignore fees/exits/sizing; not backtests or factor IC",
            "Fixed-fill cost arithmetic is not a higher-cost native replay",
            "Native wallet/signals select these campaigns; they are not IID trials",
        ],
    }
    (HERE / "terminal-daily-risk-snapshot.json").write_text(
        json.dumps(terminal_snapshot(summary["accounts"][0], plans["author100"]), indent=2) + "\n"
    )
    (HERE / "evidence.json").write_text(json.dumps(output, indent=2, default=str) + "\n")
    for r in output["accounts"]:
        print(
            json.dumps(
                {
                    k: r[k]
                    for k in (
                        "name",
                        "natural_win_rate_pct",
                        "average_natural_win_USDT",
                        "average_natural_loss_abs_USDT",
                        "fixed_horizon_diagnostic",
                    )
                },
                default=str,
            )
        )


if __name__ == "__main__":
    main()
