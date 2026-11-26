"""Source predicate diagnostics only; no fills, account replay or engine execution."""

# ruff: noqa: S101, S102
# Assertions are the diagnostic checks. exec evaluates only the pinned AST definitions.

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
DATA = Path("/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi40_data_v002/candles/futures")
START, END = pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2026-08-31 23:00", tz="UTC")
if not __debug__:
    raise RuntimeError("Run diagnostics without Python optimization; assertions are required")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_definitions():
    """Execute original AST definitions with attribute-only diagnostic stand-ins.

    No SDK engine, model validators, import side effects, fills or cash bookkeeping.
    Function and class AST bodies are unchanged. The mock context stays flat.
    """
    ns = {
        "np": np,
        "List": list,
        "Optional": Optional,
        "Dict": dict,
        "Candle": SimpleNamespace,
        "Strategy": object,
        "Signal": SimpleNamespace(HOLD="HOLD"),
        "Side": SimpleNamespace(LONG="LONG", SHORT="SHORT"),
    }
    p = ROOT / "sdk_selected/flint/indicators.py"
    names = {"closes", "highest_high", "lowest_low", "atr", "volatility"}
    funcs = [
        n
        for n in ast.parse(p.read_text()).body
        if isinstance(n, ast.FunctionDef) and n.name in names
    ]
    assert len(funcs) == len(names)
    exec(compile(ast.Module(body=funcs, type_ignores=[]), str(p), "exec"), ns)
    p = ROOT / "source/donchian_surfer.py"
    cls = [n for n in ast.parse(p.read_text()).body if isinstance(n, ast.ClassDef)]
    assert len(cls) == 1
    exec(compile(ast.Module(body=cls, type_ignores=[]), str(p), "exec"), ns)
    return ns


class ProbeContext:
    """Each invocation is an independent flat-position probe, never a wallet."""

    account = SimpleNamespace(equity=1000.0)

    def __init__(self, btc, pos=None):
        self.btc, self.pos, self.orders, self.closes = btc, pos, [], []

    def position(self, market):
        return self.pos

    def get_candles(self, market, lookback):
        assert market == "BTC-PERP"
        return self.btc[-lookback:]

    def market_order(self, market, side, size):
        self.orders.append({"market": market, "side": side, "size": size})

    def close_position(self, market):
        self.closes.append(market)


def synthetic(ns):
    rng = np.random.default_rng(48115)
    high, low = ns["highest_high"], ns["lowest_low"]
    for _ in range(1000):
        c = rng.uniform(1, 500, 60)
        h, lo = c + rng.uniform(0, 20, 60), c - rng.uniform(0, 0.99, 60)
        hist = [SimpleNamespace(close=x, high=y, low=z) for x, y, z in zip(c, h, lo, strict=True)]
        last = hist[-1]
        assert last.close <= high(hist, 48)
        assert last.close >= low(hist, 48)
        assert not last.close < low(hist, 24)
        assert not last.close > high(hist, 24)
        assert (last.close >= high(hist, 48)) == (last.close == last.high == high(hist, 48))
        assert (last.close <= low(hist, 48)) == (last.close == last.low == low(hist, 48))
    # Equality can enter; do not conclude all entries are mathematically impossible.
    history = [
        SimpleNamespace(close=100.0, high=101.0, low=99.0, market="SOL-PERP") for _ in range(58)
    ]
    history[-1] = SimpleNamespace(close=102.0, high=102.0, low=100.0, market="SOL-PERP")
    assert history[-1].close >= high(history, 48)
    history[-1] = SimpleNamespace(close=98.0, high=100.0, low=98.0, market="SOL-PERP")
    assert history[-1].close <= low(history, 48)
    # ATR trail and maximum-hold exits remain possible in the unchanged callback.
    cls = ns["DonchianSurfer"]
    pos = SimpleNamespace(side="LONG", entry_price=100.0)
    history[-1] = SimpleNamespace(close=100.0, high=101.0, low=99.0, market="SOL-PERP")
    strategy, ctx = cls(), ProbeContext([], pos)
    strategy._peak = 120.0
    strategy.on_candle(history[-1], history, ctx)
    assert ctx.closes == ["SOL-PERP"]
    strategy, ctx = cls(), ProbeContext([], pos)
    strategy._peak, strategy._bars_in_pos, strategy._losses = 101.0, 119, 1
    strategy.on_candle(history[-1], history, ctx)
    assert ctx.closes == ["SOL-PERP"] and strategy._losses == 1
    return {
        "random_valid_ohlc_histories": 1000,
        "equality_long_short_possible": True,
        "opposite_channel_exit_impossible_under_valid_ohlc": True,
        "atr_trail_exit_possible": True,
        "max_hold_exit_possible": True,
        "max_hold_preserves_loss_counter": True,
    }


def run():
    for receipt in json.loads((ROOT / "source_receipts.json").read_text())["records"]:
        assert sha(Path(receipt["local_path"])) == receipt["sha256"]
    receipt = json.loads((ROOT / "sdk_distribution_receipts.json").read_text())
    assert sha(Path(receipt["path"])) == receipt["sha256"]
    for module in receipt["selected_modules"]:
        assert sha(Path(module["path"])) == module["sha256"]
    ns = load_definitions()
    evidence = synthetic(ns)
    frames, receipts = {}, []
    for market in ["SOL", "BTC"]:
        path = DATA / f"{market}_USDT_USDT-1h-futures.feather"
        digest = sha(path)
        df = pd.read_feather(path)
        assert df.date.is_monotonic_increasing and not df.date.duplicated().any()
        assert df.date.diff().dropna().eq(pd.Timedelta(hours=1)).all()
        assert (
            (df.high >= df[["open", "close"]].max(axis=1))
            & (df.low <= df[["open", "close"]].min(axis=1))
            & (df.low > 0)
        ).all()
        frames[market] = df
        receipts.append(
            {
                "path": str(path),
                "sha256": digest,
                "rows": len(df),
                "first": str(df.date.min()),
                "last": str(df.date.max()),
            }
        )
    assert frames["SOL"].date.equals(frames["BTC"].date)
    candles = {}
    for market, df in frames.items():
        candles[market] = [
            SimpleNamespace(**row._asdict(), market=f"{market}-PERP")
            for row in df.itertuples(index=False)
        ]
    counts = {
        "bars": 0,
        "close_at_48_high": 0,
        "close_at_48_low": 0,
        "variance_ratio_pass": 0,
        "high_and_vr": 0,
        "low_and_vr": 0,
        "flat_long_order_probes": 0,
        "flat_short_order_probes": 0,
        "opposite_channel_long_exit": 0,
        "opposite_channel_short_exit": 0,
    }
    signals = []
    for i, current in enumerate(candles["SOL"]):
        if not START <= current.date <= END:
            continue
        hist, btc = candles["SOL"][max(0, i - 60) : i + 1], candles["BTC"][max(0, i - 60) : i + 1]
        assert len(hist) >= 58
        strategy, ctx = ns["DonchianSurfer"](), ProbeContext(btc)
        vr = strategy._variance_ratio(hist)
        is_high = current.close >= ns["highest_high"](hist, 48)
        is_low = current.close <= ns["lowest_low"](hist, 48)
        assert is_high == (current.close == current.high == ns["highest_high"](hist, 48))
        assert is_low == (current.close == current.low == ns["lowest_low"](hist, 48))
        counts["bars"] += 1
        counts["close_at_48_high"] += int(is_high)
        counts["close_at_48_low"] += int(is_low)
        counts["variance_ratio_pass"] += int(vr >= 1.15)
        counts["high_and_vr"] += int(is_high and vr >= 1.15)
        counts["low_and_vr"] += int(is_low and vr >= 1.15)
        counts["opposite_channel_long_exit"] += int(current.close < ns["lowest_low"](hist, 24))
        counts["opposite_channel_short_exit"] += int(current.close > ns["highest_high"](hist, 24))
        strategy.on_candle(current, hist, ctx)
        for order in ctx.orders:
            counts[
                "flat_long_order_probes" if order["side"] == "LONG" else "flat_short_order_probes"
            ] += 1
            signals.append(
                {
                    "date": str(current.date),
                    "side": order["side"],
                    "variance_ratio": float(vr),
                    "diagnostic_notional_to_mock_equity": order["size"] * current.close / 1000,
                }
            )
    assert counts["bars"] == 14592
    assert counts["opposite_channel_long_exit"] == counts["opposite_channel_short_exit"] == 0
    for receipt in receipts:
        assert sha(Path(receipt["path"])) == receipt["sha256"]
    result = {
        "status": "SOURCE_SEMANTICS_DIAGNOSTIC_ONLY",
        "window": {"start": str(START), "end": str(END)},
        "data_receipts": receipts,
        "history_contract": "current-inclusive; existing prehistory; no missing rows",
        "synthetic_checks": evidence,
        "counts": counts,
        "independent_flat_probes": signals,
        "scope": (
            "Fresh source-default instance and flat mock context each bar. "
            "No positions, cooldown replay, fills, funding, fees or PnL. "
            "Probes are not native trades or an original-window reproduction."
        ),
        "native_financial_wallets": 0,
        "source_financial_runs": 0,
        "sdk_reference": "PyPI flint-trading 1.1.0, not verified author dependency",
        "production_eligible": False,
        "inputs": {
            str(p.relative_to(ROOT)): sha(p)
            for p in [
                ROOT / "protocol.json",
                ROOT / "source/donchian_surfer.py",
                ROOT / "sdk_selected/flint/indicators.py",
                Path(__file__),
            ]
        },
    }
    (ROOT / "diagnostics.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {"status": result["status"], "counts": counts, "synthetic": evidence, "wallets": 0}
        )
    )


if __name__ == "__main__":
    run()
