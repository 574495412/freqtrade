"""Freeze and test pre-entry common market pressure on native X8 ledgers."""

import math
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi_btc_leverage_20261107 import study as leverage
from research.crypto.nfi_market_pressure_20261107 import factor
from research.crypto.nfi_solo_allocation_20261107 import long_mark
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation
from research.crypto.nfi_strength_allocation_20261107.audit import native_trades, read
from research.crypto.nfi_strength_allocation_20261107.register import BASELINES, DATA, record
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked,
    load_module,
    save,
    sha,
)


HERE = Path(__file__).resolve().parent
OUT = DATA / "nfi_market_pressure_20261107_v001"
POLICY = HERE / "registration.json"
RESULT = HERE / "result.json"
BTC = "BTC/USDT:USDT"
COSTS = (15, 8)
HORIZON = pd.Timedelta(hours=6)


def register():
    if POLICY.exists() or OUT.exists():
        raise FileExistsError("Research stage is already registered")
    known = leverage.read(leverage.RESULT)["stages"]["2024"]
    windows = {}
    for window in ("2024", "later"):
        admission = (
            leverage.a13.OUT / "5m/admission.json"
            if window == "2024"
            else long_mark.OUT / "admission.json"
        )
        accounts = {}
        for cost in COSTS:
            key = f"2024_Native2x_{cost}bps"
            if window == "2024":
                plan = leverage.check()["2024_Native2x"]
                folder = leverage.OUT / key
                audit = known["accounts"][key]
                archive_sha = audit["archive_sha256"]
                hourly = audit["hourly_equity"]
            else:
                folder = BASELINES[cost]
                plan = folder / "registered_plan.json"
                audit = read(folder / "independent-account-audit.json")
                if sha(plan) != audit["plan_sha256"]:
                    raise ValueError("Later native plan changed")
                archive_sha = audit["archive_sha256"]
                hourly = {
                    "path": str(folder / "hourly_equity.parquet"),
                    "sha256": audit["curve_sha256"],
                }
            (archive,) = (folder / "native_results").glob("*.zip")
            accounts[str(cost)] = {
                "plan": record(plan),
                "archive": {"path": str(archive), "sha256": archive_sha},
                "hourly_equity": hourly,
            }
        windows[window] = {"admission": record(admission), "accounts": accounts}
    save(
        POLICY,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "sources": [
                record(p)
                for p in (
                    Path(__file__),
                    Path(factor.__file__),
                    leverage.RESULT,
                    leverage.PROTOCOL,
                    Path(mark_valuation.__file__),
                    mark_valuation.ORACLE,
                )
            ],
            "windows": windows,
            "factor": (
                "At first order decision use the last completed five-minute bar close. "
                "Compute one-hour log returns on the exact grid, and take the median across the "
                "39 non-BTC contracts requiring at least 32 valid. Compare it to the RMS of the "
                "previous 168 non-overlapping one-hour median returns at the same minute "
                "phase. Market shock requires signed median z <= -1.5 and at least two-thirds "
                "of available alts moving against the side. BTC comparator uses its own RMS "
                "and the same signed z threshold without a breadth clause. If either z is "
                "missing, retain as unknown and exclude from all comparative event groups."
            ),
            "markout": (
                "Signed worst adverse move measured from the mark OPEN at actual first fill "
                "over the next 72 five-minute mark OPEN ticks, regardless of actual exit. "
                "A >=3% adverse move is the tail outcome. Use same 40-pair universe. "
                "Do not combine cost accounts as independent evidence. Keep censoring and "
                "native final trade PnL separate from markout."
            ),
            "gate": (
                "For both costs in both windows require at least 12 market-shock and 12 BTC "
                "shock distinct UTC dates in 2024 and at least 18 each in later window. "
                "Day-balanced median marked MAE must be >=0.75pp higher when market flags vs "
                "not. Day-balanced mean tail hit rate must exceed BTC by at least 5pp. "
                "For 2024 shadow sizing, halve market-flagged full native positions at fixed "
                "fills, fills times, sizes of all other positions, exits, and account paths. "
                "Require <=25% observed 5m drawdown, lower than original 2x, and >=60% of "
                "2x incremental full return over original 1x retained at both costs. Only "
                "joint pass justifies a separate native account experiment. Shadow scaling "
                "is an additive attribution, with no new wallet or compounding dynamics."
            ),
            "known_history": (
                "The original profits and April 2024 risk episode were known. Conditional "
                "markouts and shadow results were unknown. Later ledgers are fixed native 1x "
                "cohorts and window transfer is retrospective, not an independent 2x account."
            ),
            "orders_enabled": False,
        },
    )


def close_panel(plan):
    start = pd.Timestamp(plan["start"]) - pd.Timedelta(days=8)
    end = pd.Timestamp(plan["comparison_end"])
    grid = pd.date_range(start, end, freq="5min").as_unit("ns")
    records = read(checked(plan["data_receipts"]))
    closes = {}
    for pair in plan["pairs"]:
        stem = pair.replace("/", "_").replace(":", "_")
        rel = f"futures/{stem}-5m-futures.feather"
        path = checked({"path": str(Path(plan["datadir"]) / rel), "sha256": records[rel]})
        df = pd.read_feather(path, columns=["date", "close"])
        date = pd.DatetimeIndex(df.date).as_unit("ns") + pd.Timedelta(minutes=5)
        if date.tz is None or not date.is_unique or not date.is_monotonic_increasing:
            raise ValueError(f"Invalid available clock {pair}")
        v = pd.Series(df.close.to_numpy(dtype=float), index=date).reindex(grid)
        closes[pair] = v.where(np.isfinite(v) & (v > 0))
    return pd.DataFrame(closes, index=grid)


def verify_at(panel, result, at):
    dates = pd.date_range(end=at, periods=169, freq="h")
    old = panel.reindex(dates - pd.Timedelta(hours=1)).to_numpy()
    new = panel.reindex(dates).to_numpy()
    ret = np.log(new / old)
    k = panel.columns.get_loc(BTC)
    btc = ret[:, k]
    alts = np.delete(ret, k, axis=1)
    counts = np.isfinite(alts).sum(axis=1)
    med = pd.DataFrame(alts).median(axis=1).where(counts >= 32).to_numpy()

    def z(values):
        rms = math.sqrt(math.fsum(values[:-1] ** 2) / 168)
        return values[-1] / rms if rms > 1e-12 else math.nan

    actual = result.loc[at]
    expected = [z(btc), z(med), counts[-1], sum(alts[-1] < 0), sum(alts[-1] > 0)]
    if not np.allclose(actual, expected, atol=1e-10, rtol=1e-10, equal_nan=True):
        raise ValueError(f"Independent factor replay failed at {at}")


def trade_events(trades, prices, known, end):
    rows = []
    counts = {"end_censored": 0, "factor_unknown": 0}
    for trade in trades:
        pair = trade["pair"]
        start = pd.to_datetime(trade["open_timestamp"], unit="ms", utc=True)
        first = next(o for o in trade["orders"] if o["ft_is_entry"])
        filled = pd.to_datetime(first["order_filled_timestamp"], unit="ms", utc=True)
        row = known.loc[start]
        short = trade["is_short"]
        market, btc = factor.flags(row, short)
        admissible = bool(np.isfinite([row.market_z, row.btc_z]).all())
        counts["factor_unknown"] += not admissible
        mae = None
        if filled + HORIZON <= end:
            grid = pd.date_range(filled, filled + HORIZON, freq="5min")
            marks = prices[pair].reindex(grid).to_numpy()
            if len(marks) != 73 or not np.isfinite(marks).all() or (marks <= 0).any():
                raise ValueError(f"Incomplete markout for {pair} at {filled}")
            signed = (marks / marks[0] - 1) * (-1 if short else 1)
            mae = max(0.0, -float(min(signed[1:])))
        else:
            counts["end_censored"] += 1
        rows.append(
            {
                "pair": pair,
                "decision": start,
                "first_fill": filled,
                "short": short,
                "first_notional": float(first["amount"] * first["safe_price"]),
                "pnl_usdt": float(trade["profit_abs"]),
                "forced_exit": trade["exit_reason"] == "force_exit",
                "mae6": mae,
                "market_z": row.market_z,
                "btc_z": row.btc_z,
                "known": admissible,
                "market_shock": market if admissible else False,
                "btc_shock": btc if admissible else False,
            }
        )
    return pd.DataFrame(rows), counts


def metrics(rows):
    events = rows.loc[rows.mae6.notna() & rows.known].copy()
    events["day"] = events.decision.dt.floor("D")
    events["tail"] = events.mae6 >= 0.03
    out = {}
    for name, mask in (
        ("market_shock", events.market_shock),
        ("market_normal", ~events.market_shock),
        ("btc_shock", events.btc_shock),
        ("btc_normal", ~events.btc_shock),
    ):
        part = events.loc[mask]
        by_day = part.groupby("day")
        natural = part.loc[~part.forced_exit]
        out[name] = {
            "trades": len(part),
            "utc_days": int(part.day.nunique()),
            "day_balanced_mae6": float(by_day.mae6.median().median()) if len(part) else None,
            "day_balanced_tail_hit_rate": float(by_day["tail"].mean().mean())
            if len(part)
            else None,
            "natural_native_net_pnl_usdt": float(natural.pnl_usdt.sum()),
            "natural_first_notional_usdt": float(natural.first_notional.sum()),
        }
    return out


def screen(group, window):
    market, normal, btc = (group[k] for k in ("market_shock", "market_normal", "btc_shock"))
    days = 12 if window == "2024" else 18
    return {
        "dates": min(market["utc_days"], btc["utc_days"]) >= days,
        "adverse_excursion": (
            market["day_balanced_mae6"] is not None
            and normal["day_balanced_mae6"] is not None
            and market["day_balanced_mae6"] >= normal["day_balanced_mae6"] + 0.0075
        ),
        "tail_precision": (
            market["day_balanced_tail_hit_rate"] is not None
            and btc["day_balanced_tail_hit_rate"] is not None
            and market["day_balanced_tail_hit_rate"] >= btc["day_balanced_tail_hit_rate"] + 0.05
        ),
    }


def shadow(plan, rows, trades, prices, parent, key):
    curve = pd.read_parquet(checked(parent["marked_risk"][key]["curve"])).set_index("date")
    grid = curve.index
    oracle = load_module("pressure_mark_oracle", mark_valuation.ORACLE)
    source = Path(plan["datadir"])
    receipts = read(checked(plan["data_receipts"]))
    clocks = grid.as_unit("ms").asi8
    nav = np.full(len(grid), plan["initial_equity_usdt"], dtype=float)
    changes = {name: np.zeros(len(grid)) for name in ("market_shock", "btc_shock")}
    markets = {}
    for trade, row in zip(trades, rows.itertuples(index=False), strict=True):
        # Decimal inputs already passed the independent cash audit. The frozen
        # NumPy mark oracle uses floats from the native JSON schema.
        trade = {
            **trade,
            **{
                key: float(trade[key])
                for key in ("profit_abs", "funding_fees", "fee_open", "fee_close")
            },
            "orders": [
                {**o, "amount": float(o["amount"]), "safe_price": float(o["safe_price"])}
                for o in trade["orders"]
            ],
        }
        pair = trade["pair"]
        if pair not in markets:
            markets[pair] = (
                prices[pair].reindex(grid).to_numpy(),
                oracle.verified_frame(source, receipts, pair, "1h-funding_rate"),
                oracle.verified_frame(source, receipts, pair, "1h-mark"),
            )
        if row.market_shock or row.btc_shock:
            part = np.zeros(len(grid))
            oracle.contribute(trade, clocks, *markets[pair], part)
            nav += part
            for name in changes:
                if getattr(row, name):
                    changes[name] -= part / 2
        else:
            oracle.contribute(trade, clocks, *markets[pair], nav)
    error = float(np.max(np.abs(nav - curve.equity.to_numpy())))
    if not np.isfinite(error) or error > 1e-5:
        raise ValueError("Five-minute shadow baseline did not reconcile")
    info = parent["comparison"][key.split("_")[-1][:-3]]
    base, uniform = (info[k] for k in ("Original1x", "Native2x"))
    out = {}
    for name, change in changes.items():
        hypothetical = nav + change
        net = float(hypothetical[-1] / hypothetical[0] - 1)
        dd = float((1 - hypothetical / np.maximum.accumulate(hypothetical)).max())
        retained = (net - base["return"]) / (uniform["return"] - base["return"])
        out[name] = {
            "net_return": net,
            "drawdown_5m": dd,
            "incremental_retained": retained,
            "checks": {
                "drawdown": dd <= 0.25 and dd < uniform["drawdown"],
                "retention": retained >= 0.6,
            },
        }
    return {"baseline_curve_error_usdt": error, "results": out}


def run():
    registration = read(POLICY)
    for source in registration["sources"]:
        checked(source)
    OUT.mkdir()
    parent = read(leverage.RESULT)["stages"]["2024"]
    output = {}
    for window, specification in registration["windows"].items():
        marks = read(checked(specification["admission"]))["marks"]
        plan = read(checked(specification["accounts"]["15"]["plan"]))
        panel = close_panel(plan)
        known = factor.scores(panel, BTC)
        end = pd.Timestamp(plan["comparison_end"])
        prices = {p: pd.read_parquet(checked(marks[p]["frame"])).open for p in plan["pairs"]}
        output[window] = {}
        for cost in COSTS:
            account = specification["accounts"][str(cost)]
            baseline = checked(account["archive"]).parent.parent
            checked(account["hourly_equity"])
            native_plan = read(checked(account["plan"]))
            if any(native_plan[k] != plan[k] for k in ("pairs", "datadir", "comparison_end")):
                raise ValueError("Paired native account differs")
            strategy = "NostalgiaForInfinityX8"
            trades, _ = native_trades(native_plan, baseline, strategy, cost)
            rows, omissions = trade_events(trades, prices, known, end)
            sample = rows.decision.drop_duplicates().sort_values().iloc[::7]
            for at in sample:
                verify_at(panel, known, at)
            path = OUT / f"{window}_{cost}bps.parquet"
            rows.to_parquet(path, index=False)
            groups = metrics(rows)
            checks = screen(groups, window)
            entry = {
                "account_trades": len(trades),
                "exclusions": omissions,
                "metrics": groups,
                "screen": checks,
                "prefix_checks": len(sample),
                "events": record(path),
            }
            if window == "2024":
                key = f"2024_Native2x_{cost}bps"
                entry["fixed_fill_shadow"] = shadow(plan, rows, trades, prices, parent, key)
            output[window][str(cost)] = entry
        if window == "2024":
            at = pd.Timestamp("2024-04-12T18:35:00Z")
            verify_at(panel, known, at)
            sample = known.loc[at]
            market, btc = factor.flags(sample, False)
            stress = {
                **sample.to_dict(),
                "market_flag": market,
                "btc_flag": btc,
                "at": at.isoformat(),
            }
    passed = all(all(e["screen"].values()) for w in output.values() for e in w.values()) and all(
        all(e["fixed_fill_shadow"]["results"]["market_shock"]["checks"].values())
        for e in output["2024"].values()
    )
    save(
        RESULT,
        {
            "registration_sha256": sha(POLICY),
            "windows": output,
            "known_stress_point": stress,
            "advance_to_native": passed,
            "paper_eligible": False,
        },
    )
    print(output, stress, flush=True)


if __name__ == "__main__":
    {"register": register, "run": run}[sys.argv[1]]()
