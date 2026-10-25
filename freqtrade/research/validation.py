"""验证策略只依赖已完成历史，调用原生lookahead分析。"""

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
from freqtrade.enums import CandleType

from .data import save
from .strategies import CryptoBreakout, CryptoReversion, CryptoTrend
from .workflow import verify_freeze


def prefix_check(dataset: Path, plan: dict) -> dict:
    handler = FeatherDataHandler(dataset / "candles")
    checked = 0
    for symbol in plan["symbols"]:
        pair = symbol.removesuffix("USDT") + "/USDT:USDT"
        frame = handler.ohlcv_load(
            pair, "1h", candle_type=CandleType.FUTURES, fill_missing=False, drop_incomplete=False
        )
        for cls in (CryptoTrend, CryptoBreakout, CryptoReversion):
            strategy = cls({})

            def calculate(data, strategy=strategy, pair=pair):
                data = strategy.populate_indicators(data.copy(), {"pair": pair})
                data = strategy.populate_entry_trend(data, {"pair": pair})
                return strategy.populate_exit_trend(data, {"pair": pair})

            full = calculate(frame)
            for length in (500, len(frame) // 3, 2 * len(frame) // 3, len(frame) - 1):
                partial = calculate(frame.iloc[:length])
                pd.testing.assert_frame_equal(full.iloc[:length], partial, check_dtype=False)
                checked += 1
    return {
        "passed": True,
        "prefix_comparisons": checked,
        "scope": "有限固定切点，不等于任意输入形式化证明",
    }


def bias(run: Path, dataset: Path) -> dict:
    plan = verify_freeze(run, dataset)
    target = run / "bias"
    target.mkdir(exist_ok=False)
    prefix = prefix_check(dataset, plan)
    save(target / "prefix.json", prefix)
    csv_path = target / "native_lookahead.csv"
    config = run / "train" / "cost_3bps" / "settings.json"
    command = [
        sys.executable,
        "-m",
        "freqtrade",
        "lookahead-analysis",
        "--config",
        str(config),
        "--datadir",
        str(dataset / "candles"),
        "--strategy-list",
        *plan["candidates"],
        "--minimum-trade-amount",
        "10",
        "--targeted-trade-amount",
        "20",
        "--lookahead-analysis-exportfilename",
        str(csv_path),
    ]
    env = {key: value for key, value in os.environ.items() if not key.startswith("FREQTRADE__")}
    with (target / "native.log").open("w") as log:
        process = subprocess.run(command, env=env, stdout=log, stderr=log, timeout=600, check=False)
    passed = False
    if process.returncode == 0 and csv_path.exists():
        table = pd.read_csv(csv_path)
        if "has_bias" in table and len(table) == len(plan["candidates"]):
            passed = table["has_bias"].astype(str).str.lower().eq("false").all()
    result = {
        "prefix": prefix,
        "native_returncode": process.returncode,
        "native_lookahead_passed": bool(passed),
        "orders_enabled": False,
    }
    save(target / "result.json", result)
    return result
