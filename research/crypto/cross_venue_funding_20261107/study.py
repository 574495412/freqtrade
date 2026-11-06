"""Official public inputs for a historical paired-funding opportunity screen."""

import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from research.crypto.nfi_strength_allocation_20261107.register import DATA, record
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked, save, sha


HERE = Path(__file__).resolve().parent
OUT = DATA / "cross_venue_funding_20261107_v001"
POLICY = HERE / "registration.json"
RESULT = HERE / "result.json"
COINS = ("WIF", "FET", "BTC", "ETH", "SOL")
FUT = "https://fapi.binance.com/fapi/v1/"
SPOT = "https://api.binance.com/api/v3/klines"
HL = "https://api.hyperliquid.xyz/info"
UPSTREAM = "9af100d6822da7d2d0291a906c730ef172284ee2"
SOURCE = DATA / "external_sources_20261022/hummingbot_hummingbot/scripts/v2_funding_rate_arb.py"
SOURCE_SHA = "845c125057a1bf66c2a19bf6551477bfc124ee24aef101901601ea165bc83597"
WARM, BEGIN, SPLIT, END = (
    pd.Timestamp(t, tz="UTC") for t in ("2026-04-28", "2026-05-01", "2026-08-01", "2026-11-01")
)
HOUR = pd.Timedelta(hours=1)
COSTS = (8, 15)


def ms(t):
    return t.value // 1_000_000


def register():
    if POLICY.exists() or OUT.exists():
        raise FileExistsError("Study already frozen")
    if sha(SOURCE) != SOURCE_SHA:
        raise ValueError("Pinned upstream file differs")
    save(
        POLICY,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "sources": [record(Path(__file__)), record(SOURCE)],
            "upstream_commit": UPSTREAM,
            "universe": list(COINS),
            "windows": [
                [BEGIN.isoformat(), SPLIT.isoformat()],
                [SPLIT.isoformat(), END.isoformat()],
            ],
            "signal": (
                "Hummingbot normalization and direction, official 0.001/day entry and "
                "-0.001/day reversal exits, and 0.01 net paired profit exit. Replace "
                "unavailable forward quoted funding with last settlement known at least 5m. "
                "Normalize each rate by its elapsed previous settlement interval. Long the "
                "cheaper venue and short the dearer. Max one episode per coin; independent "
                "coins and windows. Maximum 7 days. Close at calendar endpoint."
            ),
            "cash_proxy": (
                "Equal underlying amount, 1 hour decision cycle, execute at next hour's "
                "trade OPEN proxy plus 5 seconds, fees 5bps per leg per side, slippage "
                "3 or 10bps each fill. Binance funding uses reported settlement mark; "
                "Hyperliquid uses preceding completed hourly trade close as oracle proxy. "
                "Use actual source funding event times and exclude pre-entry/post-exit events. "
                "Convert Binance USDT net leg PnL at the exit USDCUSDT rate. Report net PnL "
                "relative to one-leg average entry notional in USDC. No compounded wallet, "
                "capacity, mark-margin/liquidation, or proof of execution. Forced endpoints "
                "are kept separate in the gate and fully reported."
            ),
            "gate": (
                "All five coins need exact hourly OHLC/FX data, unique full hourly HL "
                "settlements and plausible Binance interval history. At each cost and window "
                "require >=20 distinct natural entry dates, positive mean natural after-cost "
                "pair net, and >=3 positive coin means. Passing only qualifies a later "
                "funded separate-wallet study with verified settlement and margin inputs."
            ),
            "orders_enabled": False,
        },
    )


def fetch(url, name, receipts, params=None, payload=None):
    path = OUT / f"{name}.json"
    target = url + ("?" + urlencode(params) if params else "")
    if not url.startswith(
        ("https://api.hyperliquid.xyz/", "https://fapi.binance.com/", "https://api.binance.com/")
    ):
        raise ValueError("Only registered public HTTPS market sources are accepted")
    request = Request(  # noqa: S310
        target,
        data=json.dumps(payload).encode() if payload else None,
        headers={"Content-Type": "application/json", "User-Agent": "research-public-market-data"},
    )
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20) as response:  # noqa: S310
                raw = response.read()
            data = json.loads(raw)
            if not isinstance(data, list):
                raise ValueError(f"Unexpected data from {name}")
            path.write_bytes(raw)
            receipts[name] = {"url": target, "payload": payload, "source": record(path)}
            return data
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            time.sleep(max(1 + attempt * 2, int(error.headers.get("Retry-After", 0))))
    raise RuntimeError("Unreachable")


def batches(start, end, hours):
    step = hours * HOUR
    while start < end:
        stop = min(end, start + step)
        yield start, stop
        start = stop


def candle_rows(symbol, receipts, spot=False):
    data = []
    for a, b in batches(WARM, END + HOUR, 900):
        rows = fetch(
            SPOT if spot else FUT + "klines",
            f"bn_price_{symbol}_{ms(a)}",
            receipts,
            params={
                "symbol": symbol,
                "interval": "1h",
                "startTime": ms(a),
                "endTime": ms(b) - 1,
                "limit": 1000,
            },
        )
        for t, opening, high, low, closing, _, last, *_ in rows:
            if last != t + 3_599_999:
                raise ValueError("Invalid Binance hourly candle")
            data.append((t, opening, high, low, closing))
    return price_frame(data)


def price_frame(rows):
    grid = pd.date_range(WARM, END, freq="h")
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close"])
    actual = pd.DatetimeIndex(pd.to_datetime(df.pop("date"), unit="ms", utc=True))
    if not actual.equals(grid):
        raise ValueError(f"Exact hourly calendar required, got {len(actual)}/{len(grid)}")
    frame = df.astype(float).set_axis(grid)
    if not np.isfinite(frame).all().all() or (frame <= 0).any().any():
        raise ValueError("Invalid hourly price")
    if not (
        frame.low.le(frame[["open", "close"]].min(axis=1))
        & frame.high.ge(frame[["open", "close"]].max(axis=1))
    ).all():
        raise ValueError("Invalid OHLC bounds")
    return frame


def binance_funding(symbol, receipts):
    data = []
    for a, b in batches(WARM, END + HOUR, 480):
        rows = fetch(
            FUT + "fundingRate",
            f"bn_fund_{symbol}_{ms(a)}",
            receipts,
            params={"symbol": symbol, "startTime": ms(a), "endTime": ms(b) - 1, "limit": 1000},
        )
        if any(row["symbol"] != symbol for row in rows):
            raise ValueError("Wrong Binance instrument")
        data.extend((r["fundingTime"], r["fundingRate"], r["markPrice"]) for r in rows)
    return funding_frame(data, False)


def hyperliquid(coin, receipts):
    candles = fetch(
        HL,
        f"hl_price_{coin}",
        receipts,
        payload={
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": "1h",
                "startTime": ms(WARM),
                "endTime": ms(END),
            },
        },
    )
    if any(r["s"] != coin or r["i"] != "1h" or r["T"] != r["t"] + 3_599_999 for r in candles):
        raise ValueError("Invalid Hyperliquid hourly candle")
    prices = price_frame([(r["t"], r["o"], r["h"], r["l"], r["c"]) for r in candles])
    proxy = prices.close.shift(1)
    rows = []
    for a, b in batches(WARM, END + HOUR, 336):
        time.sleep(0.25)
        data = fetch(
            HL,
            f"hl_fund_{coin}_{ms(a)}",
            receipts,
            payload={
                "type": "fundingHistory",
                "coin": coin,
                "startTime": ms(a),
                "endTime": ms(b) - 1,
            },
        )
        if any(r["coin"] != coin for r in data):
            raise ValueError("Wrong Hyperliquid instrument")
        for row in data:
            hour = pd.to_datetime(row["time"], unit="ms", utc=True).round("h")
            rows.append((row["time"], row["fundingRate"], proxy.get(hour, np.nan)))
    return prices, funding_frame(rows, True)


def funding_frame(rows, hourly):
    df = pd.DataFrame(rows, columns=["time", "rate", "price"]).astype(
        {"time": "int64", "rate": "float64", "price": "float64"}
    )
    df["actual"] = pd.to_datetime(df.time, unit="ms", utc=True)
    df["hour"] = df.actual.dt.round("h")
    steps = df.hour.diff() / HOUR
    if (
        df.hour.duplicated().any()
        or not df.hour.is_monotonic_increasing
        or (df.actual - df.hour).abs().max() > pd.Timedelta(seconds=4)
        or not steps.iloc[1:].isin((1,) if hourly else (1, 2, 4, 8)).all()
        or not np.isfinite(df.rate).all()
        or not np.isfinite(df.price.iloc[1:]).all()
        or (df.price.iloc[1:] <= 0).any()
        or df.hour.iloc[0] > WARM + 8 * HOUR
        or df.hour.iloc[-1] < END - 8 * HOUR
    ):
        raise ValueError("Funding calendar/schema did not pass admission")
    if hourly and not pd.DatetimeIndex(df.hour).equals(pd.date_range(WARM, END, freq="h")):
        raise ValueError("Incomplete Hyperliquid settlement calendar")
    df["daily"] = 24 * df.rate / steps
    return df


def known_rate(funding, grid):
    asof = funding.set_index(funding.actual + pd.Timedelta(minutes=5)).daily
    return asof.reindex(grid, method="ffill")


def settle_arrays(funding):
    return (
        funding.actual.dt.as_unit("ns").array.asi8,
        np.r_[0.0, (funding.rate * funding.price).fillna(0).cumsum()],
    )


def settled(events, start, end):
    timestamps, cumulative = events
    left = np.searchsorted(timestamps, start.as_unit("ns").value, side="right")
    right = np.searchsorted(timestamps, end.as_unit("ns").value, side="left")
    return cumulative[right] - cumulative[left]


def exit_reason(directional_rate, net, duration, boundary):
    if directional_rate < -0.001:
        return "reversal"
    if net > 0.01:
        return "take_profit"
    if duration >= pd.Timedelta(days=7):
        return "time_limit"
    return "boundary" if boundary else None


def evaluate_market(bn, hl, fbn, fhl, fx, start, end, cost):
    grid = pd.date_range(start, end, freq="h")
    rate = known_rate(fbn, grid) - known_rate(fhl, grid)
    if not np.isfinite(rate).all():
        raise ValueError("Missing causal signal history")
    events = (settle_arrays(fbn), settle_arrays(fhl))
    fee, slip = 0.0005, (cost - 5) / 10000
    rows = []
    opened = None

    def paired(at, px1, px2, conversion):
        opening, side, entry1, entry2, f0 = opened
        exit1, exit2 = px1 * (1 - side * slip), px2 * (1 + side * slip)
        basis = side * ((exit1 - entry1) / conversion - (exit2 - entry2))
        fees = fee * ((entry1 + exit1) / conversion + entry2 + exit2)
        carry1 = -side * settled(events[0], opening, at) / conversion
        carry2 = side * settled(events[1], opening, at)
        denominator = (entry1 / f0 + entry2) / 2
        return (basis + carry1 + carry2 - fees) / denominator, (
            basis / denominator,
            (carry1 + carry2) / denominator,
            fees / denominator,
        )

    for clock in grid[:-1]:
        execution = clock + HOUR
        when = execution + pd.Timedelta(seconds=5)
        if opened is None:
            if abs(rate[clock]) < 0.001 or execution > end - 2 * HOUR:
                continue
            side = -1 if rate[clock] > 0 else 1
            opened = (
                when,
                side,
                bn.open[execution] * (1 + side * slip),
                hl.open[execution] * (1 - side * slip),
                fx.open[execution],
            )
            opened_signal = rate[clock]
            continue
        opening, side, *_ = opened
        if clock < opening:
            continue
        clock_net, _ = paired(
            clock, bn.close[clock - HOUR], hl.close[clock - HOUR], fx.close[clock - HOUR]
        )
        reason = exit_reason(-side * rate[clock], clock_net, when - opening, execution == end)
        if reason is None:
            continue
        net, (basis, carry, fees) = paired(
            when, bn.open[execution], hl.open[execution], fx.open[execution]
        )
        if not np.isclose(net, basis + carry - fees, atol=1e-12):
            raise ValueError("Cash component identity failed")
        rows.append(
            {
                "open": opening.isoformat(),
                "close": when.isoformat(),
                "bn_side": side,
                "signal_diff_per_day": float(opened_signal),
                "net": float(net),
                "price_after_slippage": float(basis),
                "funding": float(carry),
                "fees": float(fees),
                "reason": reason,
            }
        )
        opened = None
    if opened is not None:
        raise ValueError("An episode remained at the boundary")
    return rows


def summary(rows):
    natural = [r for r in rows if r["reason"] != "boundary"]
    by_coin = {}
    for coin in COINS:
        subset = [r for r in natural if r["coin"] == coin]
        by_coin[coin] = {
            "episodes": len(subset),
            "mean_net": float(np.mean([r["net"] for r in subset])) if subset else None,
            "mean_funding": float(np.mean([r["funding"] for r in subset])) if subset else None,
        }
    mean = float(np.mean([r["net"] for r in natural])) if natural else None
    days = len({r["open"][:10] for r in natural})
    return {
        "natural_episodes": len(natural),
        "natural_entry_dates": days,
        "natural_mean_net": mean,
        "reasons": dict(Counter(r["reason"] for r in rows)),
        "by_coin": by_coin,
        "checks": {
            "dates": days >= 20,
            "positive": mean is not None and mean > 0,
            "breadth": sum(
                r["mean_net"] is not None and r["mean_net"] > 0 for r in by_coin.values()
            )
            >= 3,
        },
    }


def run():
    p = json.loads(POLICY.read_text())
    for source in p["sources"]:
        checked(source)
    OUT.mkdir()
    receipts, markets = {}, {}
    fx = candle_rows("USDCUSDT", receipts, spot=True)
    for coin in COINS:
        sym = coin + "USDT"
        bn = candle_rows(sym, receipts)
        fbn = binance_funding(sym, receipts)
        hl, fhl = hyperliquid(coin, receipts)
        markets[coin] = (bn, hl, fbn, fhl)
        save(OUT / "receipts.json", receipts)
        print("admitted", coin, len(bn), len(fbn), len(fhl), flush=True)
    report = {}
    for a, b in ((BEGIN, SPLIT), (SPLIT, END)):
        for cost in COSTS:
            key = f"{a.date()}_{cost}bps"
            rows = [
                {**r, "coin": coin}
                for coin, market in markets.items()
                for r in evaluate_market(*market, fx, a, b, cost)
            ]
            save(OUT / f"{key}.json", rows)
            report[key] = {"ledger": record(OUT / f"{key}.json"), **summary(rows)}
    save(
        RESULT,
        {
            "registration_sha256": sha(POLICY),
            "inputs": record(OUT / "receipts.json"),
            "windows": report,
            "advance": all(all(r["checks"].values()) for r in report.values()),
            "paper_eligible": False,
        },
    )
    print(report, flush=True)


if __name__ == "__main__":
    {"register": register, "run": run}[sys.argv[1]]()
