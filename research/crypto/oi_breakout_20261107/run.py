"""Freeze and test open-interest confirmation on six USDT-M perpetuals."""

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.calendar_portfolio import replay
from research.crypto.listing_reversal_20261107 import catalog
from research.crypto.prepare_conditional_metrics import download, parse_metrics
from research.crypto.spot_perp_flow_gap_20261107 import audit as account_audit
from research.crypto.spot_perp_flow_gap_20261107 import inputs as flow


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = ROOT / "user_data/crypto_quant"
OUT = DATA / "oi_breakout_20261107_v001"
OI = DATA / "conditional_metrics_v002"
MARKET = DATA / "funding_breakout_20261107_v001/data"
FUNDING = DATA / "spot_perp_flow_gap_20261107_v001"
PLAN = HERE / "registration.json"
RESULT = HERE / "result.json"
SYMBOLS = ("BNBUSDT", "BTCUSDT", "DOGEUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
CASES = ("Expand", "Price", "Contract")
MODEL = {
    "capital": 1000,
    "fee_bps": 5,
    "gross": 0.96,
    "max_positions": 2,
    "participation": 0.01,
    "stop_loss": 0.10,
    "drawdown_halt": 0.35,
}
RULE = {
    "lookback_hours": 168,
    "breakout_buffer": 0.002,
    "price_buffer_minutes": 5,
    "oi_buffer_minutes": 15,
    "oi_change_hours": 24,
    "minimum_oi_move": 0.03,
    "pulse_hours": 96,
    "invalidation": 0.01,
}
ARCHIVE = "https://data.binance.vision/data/futures/um/daily/metrics"


def register():
    if PLAN.exists() or OUT.exists():
        raise FileExistsError("Study already registered")
    market_manifest = read(MARKET / "receipts.json")
    oi_manifest = read(OI / "receipts.json")
    fund_manifest = read(FUNDING / "admission.json")
    sources = (
        Path(__file__),
        HERE / "audit.py",
        Path(account_audit.__file__),
        Path(flow.__file__),
        Path(catalog.__file__),
        ROOT / "research/crypto/prepare_conditional_metrics.py",
        ROOT / "research/crypto/calendar_portfolio.py",
        ROOT / "freqtrade/research/data.py",
        MARKET / "receipts.json",
        OI / "receipts.json",
        OI / "sources.json",
        FUNDING / "admission.json",
    )
    checksums = {str(path.relative_to(ROOT)): sha(path) for path in sources}
    for symbol in SYMBOLS:
        records = {
            MARKET / f"{symbol}_5m.parquet": market_manifest[f"{symbol}_5m.parquet"],
            OI / f"{symbol}.parquet": oi_manifest[f"{symbol}.parquet"],
        }
        if symbol != "BTCUSDT":
            item = fund_manifest[symbol]["frames"]["funding"]
            records[FUNDING / item["file"]] = item["sha256"]
        for path, digest in records.items():
            if sha(path) != digest:
                raise ValueError(f"Sealed data changed: {path}")
            checksums[str(path.relative_to(ROOT))] = digest
    save(
        PLAN,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "checksums": checksums,
            "symbols": SYMBOLS,
            "cases": CASES,
            "signal": RULE,
            "model": MODEL,
            "cost_bps_per_side": [8, 15],
            "windows": [
                {"name": "2024", "start": "2024-01-01", "split": "2024-07-01", "end": "2025-01-01"},
                {
                    "name": "2025-26",
                    "start": "2025-01-01",
                    "split": "2026-01-01",
                    "end": "2026-09-01",
                },
            ],
            "hypothesis": (
                "Open interest expansion confirms new risk-taking in both directions at a "
                "price-channel break, and yields stronger continuation than an unfiltered "
                "break or a break during open interest contraction."
            ),
            "signal_definition": (
                "Observe each UTC hour. The price input is the perpetual candle close known at "
                "t-5 minutes. Breakout is at least 20 bps above the maximum or at least the "
                "reciprocal buffer below the minimum of the preceding 168 hourly observations. "
                "Trigger on the first hour of a nonzero directional price edge. Read exact "
                "OI quantity at t-15min and at t-24h-15min; both finite and positive are "
                "required for all three accounts. Candidate requires at least 3% OI expansion, "
                "opposite ablation requires at least 3% contraction, and Price uses neither "
                "threshold. Missing and zero observations stay ineligible. Hold signal for up "
                "to 96 hours from the edge, until price reverses past the initial channel level "
                "by 1%, or until the account's terminal hour. New edges during an active "
                "signal do not extend or flip that signal. Reset signal state at each window."
            ),
            "execution": (
                "At most two targets, oldest valid signal wins, alphabetical tie-break, "
                "each target is 48% of NAV, no trade if missing data. Use existing exact "
                "funding clock calendar replay; when the target set changes, rebalance at "
                "the next 5m transaction close with adverse slippage and capacity. Allow "
                "the engine's existing delayed reversals and rebalances. Stop at 10% adverse "
                "price and halt at 35% 5m close-based account drawdown with delayed exits. "
                "Begin volume-constrained terminal exits one hour before cutoff."
            ),
            "comparison": (
                "Run 2024 first, from cash. At both costs require net return positive "
                "and greater than both controls, positive return in both halves, no halt, "
                "35% or lower maximum drawdown, at least 35 closed lifecycles across at least "
                "12 entry weeks, and no open terminal inventory. Require 25% net annualized "
                "at 15bps as upside hurdle. Only after passing, run the later cash-reset "
                "window under identical policy and gates. For that window download checksummed "
                "OI archives only for exact price-edge decision and 24h predecessor dates. "
                "Two historical cohorts with a fixed incumbent universe; not forward evidence."
            ),
            "orders_enabled": False,
        },
    )


def checked_plan():
    plan = read(PLAN)
    for name, checksum in plan["checksums"].items():
        if sha(ROOT / name) != checksum:
            raise ValueError(f"Frozen input changed: {name}")
    return plan


def bounds(window):
    return tuple(pd.Timestamp(window[k], tz="UTC") for k in ("start", "split", "end"))


def price_edges(bars, rule, window):
    begin, _, end = bounds(window)
    prior = begin - pd.Timedelta(hours=rule["lookback_hours"] + 1)
    index = pd.date_range(prior, end, freq="h", inclusive="left")
    # Candle indexed by its open, five minutes before its close.
    buffered = pd.Timedelta(minutes=rule["price_buffer_minutes"] + 5)
    price = bars.close.reindex(index - buffered).set_axis(index)
    if not np.isfinite(price).all() or (price <= 0).any():
        raise ValueError("Incomplete price channel")
    trailing = price.shift(1).rolling(rule["lookback_hours"])
    upper, lower = trailing.max(), trailing.min()
    factor = 1 + rule["breakout_buffer"]
    state = pd.Series(
        np.select([price >= upper * factor, price <= lower / factor], [1, -1], default=0),
        index=index,
    )
    edges = state.where(state.ne(state.shift()), 0)
    return pd.DataFrame({"price": price, "upper": upper, "lower": lower, "edge": edges}).loc[begin:]


def historical_oi(symbol):
    frame = pd.read_parquet(OI / f"{symbol}.parquet").set_index("raw_time")
    value = frame.sum_open_interest
    return value.where(value.gt(0) & frame.sum_open_interest_value_status.eq("KNOWN_RAW_VALUE"))


def required_oi(edges, rule):
    current = edges.index[edges.edge.ne(0)] - pd.Timedelta(minutes=rule["oi_buffer_minutes"])
    prior = current - pd.Timedelta(hours=rule["oi_change_hours"])
    return current.union(prior)


def extend_oi(plan, features):
    names = [
        (symbol, str(day.date()))
        for symbol, edges in features.items()
        for day in required_oi(edges, plan["signal"]).normalize().unique()
        if day.year >= 2025
    ]
    folder = OUT / "extension"
    folder.mkdir()
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda job: download(*job, folder, ARCHIVE), names))
    save(folder / "archive_receipts.json", responses)
    admitted = {s: [historical_oi(s)] for s in features}
    for receipt in responses:
        if receipt["status"] != "VERIFIED":
            raise ValueError(f"Extension input unknown: {receipt['symbol']} {receipt['day']}")
        symbol = receipt["symbol"]
        raw = Path(receipt["path"]).read_bytes()
        frame = parse_metrics(raw, symbol, receipt["day"]).set_index("raw_time")
        admitted[symbol].append(frame.sum_open_interest.where(frame.sum_open_interest.gt(0)))
    return {
        symbol: pd.concat(frames, verify_integrity=True).sort_index()
        for symbol, frames in admitted.items()
    }


def pulses(features, oi, rule, case):
    result = {}
    traces = {}
    lag = pd.Timedelta(minutes=rule["oi_buffer_minutes"])
    reference = pd.Timedelta(hours=rule["oi_change_hours"])
    for symbol, frame in features.items():
        current = frame.index - lag
        change = (
            oi[symbol].reindex(current).to_numpy()
            / oi[symbol].reindex(current - reference).to_numpy()
            - 1
        )
        admitted = np.isfinite(change)
        threshold = rule["minimum_oi_move"]
        if case == "Expand":
            admitted &= change >= threshold
        elif case == "Contract":
            admitted &= change <= -threshold
        sign = np.zeros(len(frame), dtype=np.int8)
        onset = np.full(len(frame), -1, dtype=np.int32)
        edge_times = np.flatnonzero(frame.edge.ne(0).to_numpy() & admitted)
        until = 0
        for t in edge_times:
            if t < until:
                continue
            side = int(frame.edge.iloc[t])
            level = frame.upper.iloc[t] if side == 1 else frame.lower.iloc[t]
            until = min(t + rule["pulse_hours"], len(frame))
            cutoff = level * (1 - side * rule["invalidation"])
            invalid = np.flatnonzero(side * (frame.price.iloc[t:until].to_numpy() - cutoff) < 0)
            if len(invalid):
                until = t + invalid[0]
            sign[t:until] = side
            onset[t:until] = t
        result[symbol] = (sign, onset)
        traces[symbol] = {
            "price_edges": int(frame.edge.ne(0).sum()),
            "known_oi_price_edges": int((frame.edge.ne(0) & np.isfinite(change)).sum()),
            "qualifying_edges": len(edge_times),
            "pulses": len(set(onset[onset >= 0])),
        }
    return result, traces


def targets(pulse, index):
    symbols = list(pulse)
    target = np.zeros((len(index), len(symbols)))
    for t in range(len(index) - 1):
        active = [(pulse[s][1][t], s, j) for j, s in enumerate(symbols) if pulse[s][0][t]]
        for _, symbol, j in sorted(active)[:2]:
            target[t, j] = pulse[symbol][0][t] / 2
    scheduled = (target != np.vstack([np.zeros(len(symbols)), target[:-1]])).any(axis=1)
    scheduled[-1] = True
    frame = pd.DataFrame(target, index=index, columns=["target_" + s for s in symbols])
    return frame.assign(rebalance=scheduled, risk_scale=1.0)


def inputs(plan):
    market = {s: pd.read_parquet(MARKET / f"{s}_5m.parquet") for s in plan["symbols"]}
    funding = {
        s: pd.read_parquet(FUNDING / f"{s}-funding.parquet")
        for s in plan["symbols"]
        if s != "BTCUSDT"
    }
    flow.OUT = OUT
    (OUT / "raw").mkdir()
    receipts = {}
    funding["BTCUSDT"] = flow.funding(plan, "BTCUSDT", receipts)
    path = OUT / "BTCUSDT-funding.parquet"
    funding["BTCUSDT"].to_parquet(path)
    save(OUT / "funding_admission.json", {"files": receipts, "btc_frame_sha256": sha(path)})
    return market, funding


def summarize(plan, account, window):
    nav = account["curve"].set_index("date").equity
    begin, split, end = bounds(window)
    years = (end - begin).days / 365.25
    return {
        "net_return": float(nav.at[end] / nav.at[begin] - 1),
        "first_slice_return": float(nav.at[split] / nav.at[begin] - 1),
        "second_slice_return": float(nav.at[end] / nav.at[split] - 1),
        "annualized_net_return": float((nav.at[end] / nav.at[begin]) ** (1 / years) - 1),
        "max_drawdown": float((1 - nav / nav.cummax()).max()),
        "risk_halted": account["risk_halted"],
        "completed": len(account["trades"]),
        "open_positions": len(account["positions"]),
        "entry_weeks": len(
            {pd.Timestamp(t["entry_time"]).date().isocalendar()[:2] for t in account["trades"]}
        ),
        "capacity": account["capacity"],
    }


def observe(plan, window, market, funding, features, oi):
    rows = {}
    for case in plan["cases"]:
        pulse, trace = pulses(features, oi, plan["signal"], case)
        schedule = targets(pulse, next(iter(features.values())).index)
        for cost in plan["cost_bps_per_side"]:
            name = f"{window['name']}_{case}_{cost}bps"
            path = OUT / name
            path.mkdir()
            schedule.to_parquet(path / "targets.parquet")
            account = replay(
                market,
                funding,
                schedule,
                {**plan["model"], "slippage_bps": cost - plan["model"]["fee_bps"]},
                window["start"],
                window["end"],
            )
            account["curve"].to_parquet(path / "nav.parquet", index=False)
            for field in ("events", "requests", "trades", "resume_state"):
                save(path / f"{field}.json", account[field])
            rows[name] = {
                "window": window["name"],
                "case": case,
                "cost_bps": cost,
                **summarize(plan, account, window),
                "signal_trace": trace,
                "hashes": {p.name: sha(p) for p in path.iterdir()},
            }
            print(
                name,
                rows[name]["net_return"],
                rows[name]["max_drawdown"],
                rows[name]["completed"],
                flush=True,
            )
    return rows


def evaluate(window, rows, costs):
    result = {}
    for cost in costs:
        name = window["name"]
        gap, price, down = (rows[f"{name}_{case}_{cost}bps"] for case in CASES)
        result[str(cost)] = {
            "beats_controls": gap["net_return"] > max(0, price["net_return"], down["net_return"]),
            "positive_both_slices": min(gap["first_slice_return"], gap["second_slice_return"]) > 0,
            "risk": gap["max_drawdown"] <= 0.35 and not gap["risk_halted"],
            "sample": gap["completed"] >= 35
            and gap["entry_weeks"] >= 12
            and not gap["open_positions"],
            "upside": cost != 15 or gap["annualized_net_return"] >= 0.25,
        }
    return result


def run():
    plan = checked_plan()
    OUT.mkdir()
    market, funding = inputs(plan)
    rows, gates = {}, {}
    for i, window in enumerate(plan["windows"]):
        features = {s: price_edges(bars, plan["signal"], window) for s, bars in market.items()}
        oi = extend_oi(plan, features) if i else {s: historical_oi(s) for s in market}
        rows.update(observe(plan, window, market, funding, features, oi))
        checks = evaluate(window, rows, plan["cost_bps_per_side"])
        gates[window["name"]] = checks
        passed = all(all(row.values()) for row in checks.values())
        save(
            RESULT,
            {
                "registration_sha256": sha(PLAN),
                "funding_admission_sha256": sha(OUT / "funding_admission.json"),
                "accounts": rows,
                "gates": gates,
                "extension_admitted": all(
                    all(all(check.values()) for check in row.values()) for row in gates.values()
                )
                if not i
                else True,
                "both_windows_passed": bool(i) and passed,
                "paper_eligible": False,
            },
        )
        if not passed:
            break


if __name__ == "__main__":
    {"register": register, "run": run}[sys.argv[1]]()
