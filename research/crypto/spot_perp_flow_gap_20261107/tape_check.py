"""Check cross-resolution turnover mismatches with an independent REST replay."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.spot_perp_flow_gap_20261107 import inputs
from research.crypto.spot_perp_flow_gap_20261107 import run as study


HERE = Path(__file__).resolve().parent
OUT = study.OUT / "tape_check"
COLUMNS = ("open", "high", "low", "close", "volume", "quote_volume")


def rest_day(job):
    symbol, day = job
    begin = day.value // 1_000_000
    receipts = {}
    rows = inputs.fetch_page(
        symbol,
        "5m-rest-check",
        inputs.PERP,
        {
            "symbol": symbol,
            "interval": "5m",
            "startTime": begin,
            "endTime": begin + inputs.DAY_MS - 1,
            "limit": 300,
        },
        receipts,
    )
    times = pd.date_range(day, periods=288, freq="5min")
    if not np.array_equal([r[0] for r in rows], times.as_unit("ms").asi8) or any(
        row[6] != row[0] + 299_999 for row in rows
    ):
        raise ValueError(f"Invalid independent five-minute clock for {symbol}/{day}")
    frame = pd.DataFrame(
        [[float(r[i]) for i in (1, 2, 3, 4, 5, 7)] for r in rows], index=times, columns=COLUMNS
    )
    if not np.isfinite(frame).all().all() or frame[["volume", "quote_volume"]].lt(0).any().any():
        raise ValueError(f"Invalid independent five-minute prices for {symbol}/{day}")
    return symbol, day, frame, receipts


def replay_with_rest(plan, result, data):
    differences = {}
    for name, row in result["accounts"].items():
        original_path = study.OUT / name
        window = next(w for w in plan["windows"] if w["name"] == row["window"])
        for file in ("targets.parquet", "nav.parquet", "events.json"):
            if sha(original_path / file) != row["hashes"][file]:
                raise ValueError("Original evidence hash mismatch")
        targets = pd.read_parquet(original_path / "targets.parquet")
        alt = study.replay(
            {s: item["bars"] for s, item in data.items()},
            {s: item["funding"] for s, item in data.items()},
            targets,
            {**plan["model"], "slippage_bps": row["cost_bps"] - plan["model"]["fee_bps"]},
            window["start"],
            window["end"],
        )
        curve = alt["curve"]
        original = pd.read_parquet(original_path / "nav.parquet")
        if not original.date.equals(curve.date):
            raise ValueError("Sensitivity clock differs")
        nav = curve.equity
        net = float(nav.iloc[-1] / plan["model"]["capital"] - 1)
        dd = float((1 - nav / nav.cummax()).max())
        differences[name] = {
            "rest_net_return": net,
            "net_change_percentage_points": 100 * (net - row["net_return"]),
            "rest_max_drawdown": dd,
            "drawdown_change_percentage_points": 100 * (dd - row["max_5m_close_drawdown"]),
            "max_nav_difference_usdt": float(np.max(np.abs(original.equity - nav))),
            "events_exactly_equal": read(original_path / "events.json") == alt["events"],
            "risk_halted": alt["risk_halted"],
        }
        print(name, differences[name], flush=True)
    return differences


def main():
    plan = study.checked_plan()
    result = read(study.RESULT)
    data = inputs.panel(plan)
    selected, coverage = [], {}
    threshold = 0.001
    for symbol, item in data.items():
        daily = item["perp"].quote
        observed = item["bars"].quote_volume.resample("D").sum().reindex(daily.index)
        diff = ((observed - daily) / daily).abs()
        chosen = diff[diff > threshold]
        selected.extend((symbol, day) for day in chosen.index)
        coverage[symbol] = {
            "days": len(diff),
            "selected": len(chosen),
            "max_relative_gap": diff.max(),
        }
    OUT.mkdir()
    save(
        OUT / "registration.json",
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "source_sha256": sha(Path(__file__)),
            "original_result_sha256": sha(study.RESULT),
            "relative_turnover_threshold": threshold,
            "policy": (
                "Post-result tape sensitivity. Freeze all dates whose cross-resolution quote "
                "turnover differs by >0.1%. Replace those five-minute rows using public REST, "
                "retain daily features, costs and targets, then replay all accounts."
            ),
            "selected_symbol_days": [(s, day.isoformat()) for s, day in selected],
        },
    )
    with ThreadPoolExecutor(max_workers=6) as pool:
        probes = list(pool.map(rest_day, selected))
    day_audits = []
    for symbol, day, corrected, receipts in probes:
        bars = data[symbol]["bars"]
        actual = bars.loc[corrected.index, list(COLUMNS)]
        prices_changed = (
            actual[["open", "high", "low", "close"]] != corrected[["open", "high", "low", "close"]]
        ).any(axis=1)
        public_daily = data[symbol]["perp"].quote.loc[day]
        day_audits.append(
            {
                "symbol": symbol,
                "day": day.isoformat(),
                "price_bars_different": int(prices_changed.sum()),
                "archive_quote": float(actual.quote_volume.sum()),
                "rest_quote": float(corrected.quote_volume.sum()),
                "rest_daily_quote": float(public_daily),
                "receipt": receipts,
            }
        )
        bars.loc[corrected.index, list(COLUMNS)] = corrected
    differences = replay_with_rest(plan, result, data)
    save(
        HERE / "tape_check.json",
        {
            "policy_sha256": sha(OUT / "registration.json"),
            "source_sha256": sha(Path(__file__)),
            "original_result_sha256": sha(study.RESULT),
            "coverage": coverage,
            "day_audits": day_audits,
            "accounts": differences,
        },
    )


if __name__ == "__main__":
    main()
