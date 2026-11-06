"""Source-based long/cash trend ensemble and aggressive consensus sizing."""

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.calendar_portfolio import replay
from research.crypto.listing_reversal_20261107 import catalog
from research.crypto.spot_perp_flow_gap_20261107 import audit as account_audit
from research.crypto.spot_perp_flow_gap_20261107 import inputs as flow


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = ROOT / "user_data/crypto_quant"
OUT = DATA / "donchian_consensus_20261107_v001"
SOURCE = DATA / "spot_baseline_data_long_20261105_v001"
PAPER = "https://concretumgroup.com/wp-content/uploads/2026/02/Catching-Crypto-Trends.pdf"
PLAN = HERE / "registration.json"
RESULT = HERE / "result.json"
HORIZONS = (5, 10, 20, 30, 60, 90, 150, 250, 360)
CASES = ("Source25", "Fixed50", "Consensus")
MODEL = {
    "capital": 1000,
    "fee_bps": 10,
    "gross": 0.98,
    "max_positions": 20,
    "participation": 0.01,
    "stop_loss": 1.0,
    "drawdown_halt": 0.40,
}
WINDOWS = (
    {"name": "2025-26", "start": "2025-01-01", "split": "2026-01-01", "end": "2026-09-01"},
    {"name": "late-65d", "start": "2026-09-01", "split": None, "end": "2026-11-05"},
)


def register():
    if PLAN.exists() or OUT.exists():
        raise FileExistsError("Study already registered")
    manifest_path = SOURCE / "data-receipts.json"
    manifest = read(manifest_path)
    names = sorted(name for name in manifest if name.endswith(("-1d.feather", "-5m.feather")))
    symbols = [
        name.removesuffix("_USDT-1d.feather") + "USDT"
        for name in names
        if name.endswith("-1d.feather")
    ]
    if len(symbols) != 36 or len(names) != 72:
        raise ValueError("Frozen spot cohort differs")
    sources = (
        Path(__file__),
        HERE / "audit.py",
        Path(account_audit.__file__),
        Path(flow.__file__),
        Path(catalog.__file__),
        ROOT / "research/crypto/calendar_portfolio.py",
        ROOT / "freqtrade/research/data.py",
        manifest_path,
    )
    checksums = {str(path.relative_to(ROOT)): sha(path) for path in sources}
    for name in names:
        path = SOURCE / "candles" / name
        if sha(path) != manifest[name]:
            raise ValueError(f"Frozen market file differs: {name}")
        checksums[str(path.relative_to(ROOT))] = manifest[name]
    document = catalog.fetch(PAPER)
    if not document.startswith(b"%PDF-"):
        raise ValueError("Invalid author paper")
    OUT.mkdir()
    source_path = OUT / "source.pdf"
    source_path.write_bytes(document)
    save(
        PLAN,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "paper": {
                "url": PAPER,
                "revision": "2025-04-09",
                "sha256": sha(source_path),
                "sections": ["4.1", "4.2", "4.3", "5.2", "7"],
            },
            "checksums": checksums,
            "symbols": symbols,
            "windows": WINDOWS,
            "cases": CASES,
            "model": MODEL,
            "horizons": HORIZONS,
            "total_cost_bps_per_side": [13, 20],
            "data_start": "2024-12-01",
            "signal": (
                "Each completed UTC daily close at the rolling highest close activates its "
                "independent long sleeve. Compare the close to the previously active trailing "
                "stop; otherwise raise it to max(previous stop, current channel midpoint). "
                "For a new long initialize the stop at that midpoint. If close is both a new "
                "high and through the old stop, give the high priority as in the published "
                "piecewise definition. Sleeve weight is active count/9. Size by a rolling "
                "90-day sample standard deviation of simple close returns times sqrt(365), "
                "with scale capped at 2 per paper. Source25 targets 25% annual asset vol; "
                "Fixed50 targets 50%; Consensus targets 75% when at least six of nine sleeves "
                "are active and 25% otherwise. The last accepted scale is the reference for "
                "the 20% relative no-trade band on volatility-only changes. Signal state "
                "changes, monthly resets, and liquidity exits bypass the band."
            ),
            "universe": (
                "At 01:00 UTC on the first day of each month, rank the 36 existing Binance "
                "spot pairs by median quote turnover in the preceding calendar month. "
                "Eligibility requires 365 completed local days, and trailing 30-day median "
                "turnover >=2m USDT. Use top 20, alphabetical ties. Remove an asset through "
                "month end if trailing 30-day median daily quote volume drops below 1m or "
                "median absolute daily return drops below 0.5%. Maintain 1/20 target budget "
                "per name and keep unallocated weight as cash. Observe all signals after a "
                "one-hour delay. Reuse the historical state when a new cash book begins."
            ),
            "adaptation": (
                "Source25 is a local paper-derived control. Binance spot fixed survivor universe "
                "substitutes for the global CoinMarketCap panel. Single shared wallet with daily "
                "portfolio weights substitutes for independent monthly drifting sleeves. The "
                "relative no-trade band uses previously approved target scale; interpret the "
                "paper's price-change screen as absolute returns. Apply long-only cash cap and "
                "common 40% portfolio halt. Use the same method in both reset windows."
            ),
            "execution": (
                "Existing five-minute calendar replay, long/cash only, no funding, no leverage, "
                "max 20 names, aggregate target capped at 98%. Orders use the next completed "
                "five-minute close, adverse slippage of 3/10bps, and 10bps quote fee. "
                "Volume times candle low conservatively bounds quote turnover. Require 1% "
                "capacity; no certified lot/minimum-notional or order book. Start terminal exits "
                "at 23:00 UTC last day; sample NAV through midnight. Stops come from daily "
                "channel and account drawdown, not the replay's price stop set at zero."
            ),
            "decision": (
                "At both costs and in both windows Consensus must beat the positive return and "
                "return/drawdown of Source25 and Fixed50, hold positive return in both slices "
                "of the long window, have no halt and drawdown <=40%, and finish flat. "
                "Require >=25% annualized net in the long window at 20bps and >=20 complete "
                "lifecycles long/short windows. The recent short window is an unannualized "
                "gate. No live or paper admission without external evidence."
            ),
            "candidate_economics_observed": False,
            "orders_enabled": False,
        },
    )


def checked_plan():
    plan = read(PLAN)
    for name, digest in plan["checksums"].items():
        if sha(ROOT / name) != digest:
            raise ValueError(f"Frozen study input differs: {name}")
    if sha(OUT / "source.pdf") != plan["paper"]["sha256"]:
        raise ValueError("Author paper changed")
    return plan


def capture(plan):
    flow.OUT = OUT
    (OUT / "raw").mkdir()

    def capture_one(symbol):
        receipts = {}
        daily = flow.daily(plan, symbol, "spot", flow.SPOT, receipts)
        name = symbol.removesuffix("USDT") + "_USDT-1d.feather"
        verified = pd.read_feather(SOURCE / "candles" / name).set_index("date")
        if not np.allclose(
            verified.close.reindex(daily.index), daily.close, rtol=1e-11, atol=1e-12
        ):
            raise ValueError(f"Binance daily close conflicts with the frozen archive: {symbol}")
        path = OUT / f"{symbol}-daily.parquet"
        daily.to_parquet(path)
        return symbol, {"frame_sha256": sha(path), "pages": receipts, "rows": len(daily)}

    with ThreadPoolExecutor(max_workers=6) as pool:
        receipt = dict(pool.map(capture_one, plan["symbols"]))
    save(OUT / "admission.json", receipt)


def inputs(plan):
    admission = read(OUT / "admission.json")
    market, closes, quote = {}, {}, {}
    for symbol in plan["symbols"]:
        path = OUT / f"{symbol}-daily.parquet"
        if sha(path) != admission[symbol]["frame_sha256"]:
            raise ValueError("Daily turnover frame differs")
        for name, receipt in admission[symbol]["pages"].items():
            if sha(OUT / "raw" / name) != receipt["sha256"]:
                raise ValueError("Daily raw evidence differs")
        native = symbol.removesuffix("USDT") + "_USDT"
        bars = pd.read_feather(SOURCE / "candles" / f"{native}-5m.feather").set_index("date")
        if (
            bars.index.has_duplicates
            or not bars.index.to_series().diff().dropna().eq(pd.Timedelta(minutes=5)).all()
        ):
            raise ValueError("Spot five-minute tape has gaps or duplicate clocks")
        market[symbol] = bars.assign(quote_volume=bars.volume * bars.low)
        closes[symbol] = (
            pd.read_feather(SOURCE / "candles" / f"{native}-1d.feather").set_index("date").close
        )
        quote[symbol] = pd.read_parquet(path).quote
    close = pd.DataFrame(closes)
    if close.isna().any().any() or (close <= 0).any().any():
        raise ValueError("Incomplete source daily warmup")
    return market, close, pd.DataFrame(quote)


def active_sleeve(close, horizon):
    trailing = close.rolling(horizon)
    high, low = trailing.max(), trailing.min()
    state = np.zeros(len(close), dtype=np.int8)
    stop = None
    for i, (price, hi, lo) in enumerate(zip(close, high, low, strict=True)):
        if not np.isfinite(hi):
            continue
        midpoint = (hi + lo) / 2
        if price == hi:
            stop = midpoint if stop is None else max(stop, midpoint)
        elif stop is not None:
            stop = None if price <= stop else max(stop, midpoint)
        state[i] = stop is not None
    return state


def strategy_inputs(close, horizons):
    votes = {s: np.sum([active_sleeve(close[s], days) for days in horizons], axis=0) for s in close}
    vol = close.pct_change(fill_method=None).rolling(90).std() * np.sqrt(365)
    return pd.DataFrame(votes, index=close.index), vol


def build_schedule(plan, close, volume, counts, volatility, window, case):
    begin, end = (pd.Timestamp(window[k], tz="UTC") for k in ("start", "end"))
    calendar = pd.date_range(begin, end, freq="h", inclusive="left")
    days = pd.date_range(begin, end, freq="D", inclusive="left")
    symbols = plan["symbols"]
    weights = pd.DataFrame(0.0, index=calendar, columns=symbols)
    decision = pd.Series(False, index=calendar)
    median_turnover = volume.rolling(30).median()
    median_move = close.pct_change(fill_method=None).abs().rolling(30).median()
    cohort = set()
    approved, previous_votes = pd.Series(0.0, index=symbols), pd.Series(-1, index=symbols)
    for day in days:
        previous_day = day - pd.Timedelta(days=1)
        monthly = day.day == 1
        if monthly:
            first = day - pd.offsets.MonthBegin(1)
            past = volume.loc[first:previous_day].median()
            history = close.loc[:previous_day].count()
            eligible = past[(median_turnover.loc[previous_day] >= 2e6) & (history >= 365)]
            cohort = set(eligible.sort_values(ascending=False, kind="stable").head(20).index)
        cohort = {
            s
            for s in cohort
            if median_turnover.at[previous_day, s] >= 1e6
            and median_move.at[previous_day, s] >= 0.005
        }
        votes = counts.loc[previous_day]
        vol = volatility.loc[previous_day]
        if case == "Consensus":
            desired = pd.Series(np.where(votes >= 6, 0.75, 0.25), index=symbols)
        else:
            desired = 0.25 if case == "Source25" else 0.50
        scale = (desired / vol.where(vol > 0)).clip(upper=2).fillna(0)
        update = (votes != previous_votes) | ((scale - approved).abs() >= approved * 0.20)
        approved = scale.where(update | monthly, approved)
        target = votes / len(plan["horizons"]) * approved / 20
        target = target.where(target.index.isin(cohort), 0)
        divisor = max(1.0, target.sum() / plan["model"]["gross"])
        clock = day + pd.Timedelta(hours=1)
        weights.loc[clock] = target / divisor / plan["model"]["gross"]
        decision.loc[clock] = True
        previous_votes = votes
    decision.iloc[-1] = True
    return weights.add_prefix("target_").assign(rebalance=decision, risk_scale=1.0)


def metrics(account, window, capital):
    curve = account["curve"].set_index("date")
    equity = curve.equity
    start, end = equity.index[[0, -1]]
    split = pd.Timestamp(window["split"], tz="UTC") if window["split"] else None
    fills = [r for r in account["events"] if r["type"] in ("entry", "exit")]
    return {
        "net_return": float(equity.iloc[-1] / capital - 1),
        "annualized_net_return": float(
            (equity.iloc[-1] / capital) ** (365.25 / (end - start).days) - 1
        )
        if split is not None
        else None,
        "slices": [
            float(equity.loc[split] / capital - 1),
            float(equity.iloc[-1] / equity.loc[split] - 1),
        ]
        if split is not None
        else [],
        "max_drawdown": float((1 - equity / equity.cummax()).max()),
        "mean_spot_exposure": float((curve.gross_exposure / equity).mean()),
        "risk_halted": account["risk_halted"],
        "completed": len(account["trades"]),
        "open_positions": len(account["positions"]),
        "small_fills_below_5usdt": int(sum(r["notional"] < 5 for r in fills)),
        "fills": len(fills),
        "capacity": account["capacity"],
    }


def load_completed(path, schedule, model, window, preserved):
    for file in (
        "targets.parquet",
        "nav.parquet",
        "resume_state.json",
        "events.json",
        "requests.json",
        "trades.json",
    ):
        artifact = path / file
        if sha(artifact) != preserved[str(artifact.relative_to(OUT))]:
            raise ValueError(f"Preserved first-attempt artifact changed: {artifact}")
    pd.testing.assert_frame_equal(
        schedule, pd.read_parquet(path / "targets.parquet"), check_freq=False
    )
    state = read(path / "resume_state.json")
    if state["config"] != model or pd.Timestamp(state["next_clock"]) != pd.Timestamp(
        window["end"], tz="UTC"
    ):
        raise ValueError("Preserved account contract or terminal clock differs")
    counters = state["counters"]
    return {
        "curve": pd.read_parquet(path / "nav.parquet"),
        "events": read(path / "events.json"),
        "trades": read(path / "trades.json"),
        "positions": state["positions"],
        "risk_halted": state["risk_halted"],
        "capacity": {
            "entry_requests": counters["entry_requests"],
            "partially_filled_entries": counters["partial"],
            "partial_exit_intervals": counters["exit_partial"],
            "max_exit_delay_minutes": counters["max_delay"],
            "requested_entry_notional": counters["wanted"],
            "filled_entry_notional": counters["filled"],
            "filled_request_ratio": counters["filled"] / counters["wanted"]
            if counters["wanted"]
            else None,
            "participation_cap": model["participation"],
            "actual_order_book_verified": False,
        },
    }


def run():
    plan = checked_plan()
    market, close, quote = inputs(plan)
    counts, volatility = strategy_inputs(close, plan["horizons"])
    funding = {
        s: pd.DataFrame(columns=["mark", "funding_rate"], index=pd.DatetimeIndex([], tz="UTC"))
        for s in market
    }
    accounts, gates = {}, {}
    preserved = {}
    if amendment := plan.get("technical_amendment"):
        first_attempt = OUT / "first-attempt-account-receipts.json"
        if sha(first_attempt) != amendment["first_attempt_receipts_sha256"]:
            raise ValueError("Preserved first-attempt receipts differ")
        preserved = read(first_attempt)
    for window in plan["windows"]:
        for case in plan["cases"]:
            schedule = build_schedule(plan, close, quote, counts, volatility, window, case)
            for cost in plan["total_cost_bps_per_side"]:
                name = f"{window['name']}_{case}_{cost}bps"
                path = OUT / name
                model = {**plan["model"], "slippage_bps": cost - plan["model"]["fee_bps"]}
                if path.exists():
                    result = load_completed(path, schedule, model, window, preserved)
                else:
                    path.mkdir()
                    schedule.to_parquet(path / "targets.parquet")
                    result = replay(
                        market, funding, schedule, model, window["start"], window["end"]
                    )
                    result["curve"].to_parquet(path / "nav.parquet", index=False)
                    for field in ("events", "requests", "trades", "resume_state"):
                        save(path / f"{field}.json", result[field])
                row = metrics(result, window, plan["model"]["capital"])
                accounts[name] = {
                    **row,
                    "case": case,
                    "cost_bps": cost,
                    "window": window["name"],
                    "hashes": {p.name: sha(p) for p in path.iterdir()},
                }
                print(name, row["net_return"], row["max_drawdown"], row["completed"], flush=True)
        for cost in plan["total_cost_bps_per_side"]:
            prefix = window["name"]
            a, b, c = (accounts[f"{prefix}_{case}_{cost}bps"] for case in plan["cases"])
            gates[f"{prefix}_{cost}bps"] = {
                "beats_returns": c["net_return"] > max(0, a["net_return"], b["net_return"]),
                "beats_return_drawdown": all(
                    c["net_return"] * d["max_drawdown"] > d["net_return"] * c["max_drawdown"]
                    for d in (a, b)
                ),
                "positive_slices": all(v > 0 for v in c["slices"]),
                "risk": not c["risk_halted"] and c["max_drawdown"] <= 0.40,
                "upside": c["annualized_net_return"] is None
                or cost != 20
                or c["annualized_net_return"] >= 0.25,
                "sample": c["completed"] >= 20 and not c["open_positions"],
            }
        save(
            RESULT,
            {
                "registration_sha256": sha(PLAN),
                "admission_sha256": sha(OUT / "admission.json"),
                "accounts": accounts,
                "gates": gates,
                "both_windows_passed": len(gates) == 4
                and all(all(d.values()) for d in gates.values()),
                "paper_eligible": False,
            },
        )


if __name__ == "__main__":
    {"register": register, "capture": lambda: capture(checked_plan()), "run": run}[sys.argv[1]]()
