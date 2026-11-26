"""Verify complete source prehistory in the native strategy extension interface."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


STUDY = Path(__file__).parent.resolve()
ROOT = STUDY.parents[2]
ENGINE = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")
sys.path.insert(0, str(STUDY))
sys.path.insert(0, str(ENGINE))

from HawkesStrategy import HawkesStrategy  # noqa: E402
from HawkesStrategyOffline import HawkesStrategyOffline  # noqa: E402

import freqtrade.strategy.interface as official_interface  # noqa: E402


DATA = ROOT / "user_data/crypto_quant/nfi_carry_matched_native_20261115_v1/candles/futures"
COLUMNS = ["hawkes_atr", "hawkes_norm_range", "hawkes_v_hawk", "hawkes_signal"]
START = pd.Timestamp("2024-12-31T23:00:00Z")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def config(pair, path):
    return {
        "research_hawkes": True,
        "dry_run": True,
        "trading_mode": "futures",
        "runmode": "backtest",
        "research_hawkes_history": {pair: {"path": str(path), "sha256": sha(path)}},
    }


def require_rejected(strategy, frame, pair, name):
    try:
        strategy.populate_indicators(frame.copy(), {"pair": pair})
    except (ValueError, KeyError):
        return name
    raise AssertionError(f"Invalid offline history was admitted: {name}")


def main():
    owner = Path(official_interface.__file__).resolve()
    if not owner.is_relative_to(ENGINE):
        raise AssertionError("The strategy did not inherit the fixed official IStrategy")
    if HawkesStrategy.startup_candle_count != 12455:
        raise AssertionError("The original attempted strategy was modified")
    if HawkesStrategyOffline.startup_candle_count != 0:
        raise AssertionError("Offline strategy still asks the exchange for prehistory")
    records = []
    rejected = []
    for asset in ["BTC", "ETH"]:
        pair = f"{asset}/USDT:USDT"
        path = DATA / f"{asset}_USDT_USDT-1h-futures.feather"
        full = pd.read_feather(path)
        native = full.loc[full.date >= START].copy()
        cfg = config(pair, path)
        strategy = HawkesStrategyOffline(cfg)
        reference = HawkesStrategy(cfg).populate_indicators(full.copy(), {"pair": pair})
        expected = reference.loc[reference.date >= START, COLUMNS]
        actual = strategy.populate_indicators(native.copy(), {"pair": pair})
        pd.testing.assert_frame_equal(actual[COLUMNS], expected, check_exact=True)
        pd.testing.assert_frame_equal(actual[native.columns], native, check_exact=True)
        assembled, count = strategy._assemble_history(native, pair)
        if count != 12455 or len(assembled) != len(full):
            raise AssertionError("The fixed 12455-hour prehistory was not preserved")
        pd.testing.assert_frame_equal(assembled, full, check_exact=True)
        prefix_cases = [1, 241, 10481]
        for size in prefix_cases:
            prefix = strategy.populate_indicators(native.iloc[:size].copy(), {"pair": pair})
            pd.testing.assert_frame_equal(
                prefix[COLUMNS], actual[COLUMNS].iloc[:size], check_exact=True
            )
        custom_index = native.iloc[:241].copy()
        custom_index.index = pd.Index(np.arange(len(custom_index)) * 2 + 111)
        custom_result = strategy.populate_indicators(custom_index.copy(), {"pair": pair})
        if not custom_result.index.equals(custom_index.index):
            raise AssertionError("Native custom index changed")
        np.testing.assert_array_equal(
            custom_result[COLUMNS].to_numpy(), actual[COLUMNS].iloc[:241].to_numpy()
        )
        with tempfile.TemporaryDirectory(prefix="hawkes_offline_history_") as directory:
            temporary = Path(directory)
            shocked = full.copy()
            shock_start = native.date.iloc[241]
            shocked.loc[shocked.date >= shock_start, ["open", "high", "low", "close"]] *= 4
            shock_path = temporary / "shocked.feather"
            shocked.to_feather(shock_path)
            shock_strategy = HawkesStrategyOffline(config(pair, shock_path))
            shock_prefix = shock_strategy.populate_indicators(
                native.iloc[:241].copy(), {"pair": pair}
            )
            pd.testing.assert_frame_equal(
                shock_prefix[COLUMNS], actual[COLUMNS].iloc[:241], check_exact=True
            )
            shock_native = shocked.loc[shocked.date >= START].copy()
            shock_full = shock_strategy.populate_indicators(shock_native, {"pair": pair})
            pd.testing.assert_frame_equal(
                shock_full[COLUMNS].iloc[:241], actual[COLUMNS].iloc[:241], check_exact=True
            )
            wrong_origin = temporary / "wrong_origin.feather"
            full.iloc[1:].reset_index(drop=True).to_feather(wrong_origin)
            rejected.append(
                require_rejected(
                    HawkesStrategyOffline(config(pair, wrong_origin)),
                    native,
                    pair,
                    f"{asset}_origin",
                )
            )
        mismatch = native.copy()
        mismatch.loc[mismatch.index[10], "volume"] += 1
        rejected.append(require_rejected(strategy, mismatch, pair, f"{asset}_OHLCV"))
        rejected.append(
            require_rejected(strategy, native.drop(native.index[10]), pair, f"{asset}_native_gap")
        )
        wrong_sha = config(pair, path)
        wrong_sha["research_hawkes_history"][pair]["sha256"] = "0" * 64
        rejected.append(
            require_rejected(HawkesStrategyOffline(wrong_sha), native, pair, f"{asset}_SHA")
        )
        records.append(
            {
                "pair": pair,
                "input_path": str(path),
                "input_sha256": sha(path),
                "full_rows": len(full),
                "native_rows": len(native),
                "prehistory_rows": count,
                "prehistory_origin": str(assembled.date.iloc[0]),
                "native_start": str(native.date.iloc[0]),
                "exact_full_source_tail": True,
                "exact_native_OHLCV_preserved": True,
                "prefix_lengths_exact": prefix_cases,
                "custom_index_preserved": True,
                "future_history_and_native_shock_prefix_exact": True,
            }
        )
    receipt = {
        "verified_at": datetime.now(UTC).isoformat(),
        "passed": True,
        "scope": (
            "Official IStrategy subclass feature/history integration; "
            "no Backtesting.start or account economics"
        ),
        "official_IStrategy_path": str(owner),
        "records": records,
        "rejected_cases": rejected,
        "source_signal_or_parameters_changed": False,
        "exchange_capability_modified": False,
        "original_strategy_unchanged": {
            "path": str(STUDY / "HawkesStrategy.py"),
            "sha256": sha(STUDY / "HawkesStrategy.py"),
        },
        "files": {
            name: sha(STUDY / name)
            for name in ["HawkesStrategyOffline.py", "test_offline_history.py", "HawkesInputs.py"]
        },
        "new_native_accounts_completed": 0,
        "higher_return_proven": False,
    }
    with (STUDY / "offline_history_validation.json").open("x") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
