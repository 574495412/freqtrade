"""Read-only source/configuration audit against sealed official-engine backtest archives."""

import json
import os
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from freqtrade.enums import RunMode
from freqtrade.resolvers import StrategyResolver
from research.crypto.paper_followup_20261109.snapshot import ref, save


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
RAW = ROOT / "user_data/crypto_quant/runtime_backtest_parity_20261109_v001"
FIELDS = (
    "dry_run_wallet",
    "grinding_enable",
    "max_open_trades",
    "stake_currency",
    "stake_amount",
    "tradable_balance_ratio",
    "timeframe",
    "unfilledtimeout",
    "order_types",
    "entry_pricing",
    "exit_pricing",
    "trading_mode",
    "margin_mode",
    "pairlists",
    "fee",
    "nfi_parameters",
    "force_entry_enable",
)
ATTRS = (
    "timeframe",
    "startup_candle_count",
    "process_only_new_candles",
    "can_short",
    "minimal_roi",
    "stoploss",
    "trailing_stop",
    "use_custom_stoploss",
    "position_adjustment_enable",
    "max_entry_position_adjustment",
    "use_exit_signal",
    "exit_profit_only",
    "ignore_roi_if_entry_signal",
    "order_types",
    "order_time_in_force",
)
CASES = {
    "spot": "spot_baseline_long_run_20261105_v001/nfi-x8-spot-cost13",
    "futures": "wide_strategy_comparison_20261105_v001/nfi-x8-1x-cost8",
}


def load_checked(reference):
    path = Path(reference["path"])
    if ref(path) != reference:
        raise ValueError("Frozen input changed")
    return json.loads(path.read_text())


def effective(config, mode, strategy_path):
    with TemporaryDirectory(prefix="nfi-parity-schema-") as folder:
        temporary = {
            **config,
            "runmode": mode,
            "user_data_dir": Path(folder),
            "strategy_path": str(strategy_path),
        }
        strategy = StrategyResolver.load_strategy(temporary)
        return {key: getattr(strategy, key, None) for key in ATTRS}


def audit_market(market, snapshot):
    archive = next(
        (ROOT / "user_data/crypto_quant" / CASES[market] / "native_results").glob("*.zip")
    )
    with ZipFile(archive) as stream:
        config_name = next(n for n in stream.namelist() if n.endswith("_config.json"))
        strategy_name = next(
            n for n in stream.namelist() if n.endswith("_NostalgiaForInfinityX8.py")
        )
        backtest = json.loads(stream.read(config_name))
        strategy_bytes = stream.read(strategy_name)
    current = load_checked(snapshot["accounts"][market + "_original"]["config"])
    deployed = ROOT / "deploy/strategy/nfi-x8/NostalgiaForInfinityX8.py"
    checks = {
        key: {
            "backtest": backtest.get(key),
            "runtime": current.get(key),
            "equal": backtest.get(key) == current.get(key),
        }
        for key in FIELDS
    }
    for key in ("name", "pair_whitelist", "pair_blacklist", "enable_ws"):
        old, new = backtest["exchange"].get(key), current["exchange"].get(key)
        checks["exchange." + key] = {"backtest": old, "runtime": new, "equal": old == new}
    old = effective(backtest, RunMode.BACKTEST, deployed.parent)
    new = effective(current, RunMode.DRY_RUN, deployed.parent)
    resolved = {
        key: {"backtest": old[key], "runtime": new[key], "equal": old[key] == new[key]}
        for key in ATTRS
    }
    ledger = load_checked(snapshot["accounts"][market + "_original"]["ledger"])
    return {
        "archive": ref(archive),
        "runtime_strategy": ref(deployed),
        "strategy_bytes_equal_to_archived_backtest": strategy_bytes == deployed.read_bytes(),
        "economic_config_checks": checks,
        "resolved_strategy_checks": resolved,
        "all_compared_economic_config_equal": all(x["equal"] for x in checks.values()),
        "all_compared_resolved_strategy_equal": all(x["equal"] for x in resolved.values()),
        "native_fill_fee_rates_match_backtest": all(
            t["fee_open"] == backtest["fee"] and t["fee_close"] == backtest["fee"]
            for t in ledger["trades"]
        ),
        "actual_native_trade_leverage_all_one": all(t["leverage"] == 1 for t in ledger["trades"]),
        "transport_difference": {
            "backtest_https_proxy": backtest["exchange"]["ccxt_config"].get("httpsProxy"),
            "runtime_https_proxy": current["exchange"]["ccxt_config"].get("httpsProxy"),
        },
    }


def actual_processes(snapshot):
    results = []
    for key, account in snapshot["accounts"].items():
        market, version = account["market"], account["version"]
        label = "io.freqtrade.paper." + ("x8-addcap." if version == "addcap" else "") + market
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            capture_output=True,
            text=True,
            check=False,
        )
        pid = re.search(r"^\s*pid = (\d+)$", result.stdout, re.MULTILINE)
        if result.returncode or not pid:
            raise ValueError("Expected native paper worker is not running")
        command = subprocess.run(
            ["ps", "-p", pid.group(1), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        config = load_checked(account["config"])
        if "--dry-run" not in command or config["dry_run"] is not True:
            raise ValueError("Runtime account mode changed; review before reporting")
        results.append(
            {"account": key, "pid": int(pid.group(1)), "cli_dry_run": True, "config_dry_run": True}
        )
    return results


def main():
    snapshot = json.loads((RAW / "snapshot.json").read_text())
    market_checks = {market: audit_market(market, snapshot) for market in CASES}
    checks = []
    for launcher in ("deploy/launch.py", "deploy/nfi_x8_addcap/launch.py"):
        for market in CASES:
            result = subprocess.run(
                [str(ROOT / ".venv-paper/bin/python"), "-B", launcher, market, "--verify"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=60,
            )
            if result.returncode:
                raise ValueError("Native release/source validation failed")
            checks.append(
                {"launcher": launcher, "market": market, "receipt": json.loads(result.stdout)}
            )
    gap = json.loads((ROOT / "research/crypto/paper_followup_20261109/health.json").read_text())
    lookahead = ROOT / "docs/research-20261105/crypto-wide-comparison-20261105"
    save(
        HERE / "parity.json",
        {
            "scope": "The four local NFI accounts deployed in this task.",
            "snapshot": ref(RAW / "snapshot.json"),
            "market_checks": market_checks,
            "actual_processes": actual_processes(snapshot),
            "release_checks": checks,
            "running_mode": "dry_run",
            "real_orders_enabled": False,
            "full_execution_parity_verified": False,
            "same_window_signal_order_replay_completed": False,
            "optimized_wrapper_is_baseline_identical": False,
            "optimized_wrapper": ref(ROOT / "deploy/nfi_x8_addcap/strategy/NFIX8AddCapPaper.py"),
            "execution_differences": [
                "Backtests use candle-based fills; dry-run uses ongoing market prices.",
                "Actual fills can occur after submission and between candle boundaries.",
                "Persistent positions, wallet and NFI cache differ from a fresh backtest.",
                "Observed heartbeat gaps prevent claiming continuous forward validation.",
                "AddCap deliberately changes positive position-adjustment decisions.",
            ],
            "previous_maximum_observed_heartbeat_gap_seconds": max(
                s["maximum_observed_heartbeat_gap_seconds_since_repair"] for s in gap["services"]
            ),
            "unresolved_lookahead": {
                "spot_exit_time_anomalies": 3,
                "futures_exit_time_anomalies": 3,
                "meaning": "Unexplained diagnostic failures; not proven future reads or cleared.",
                "interpretation": ref(lookahead / "official-lookahead-interpretation-20261105.md"),
            },
            "official_references": [
                "https://www.freqtrade.io/en/stable/backtesting/#assumptions-made-by-backtesting",
                "https://www.freqtrade.io/en/stable/configuration/#using-dry-run-mode",
            ],
            "new_backtests": 0,
            "native_strategy_configuration_changed": False,
        },
    )
    for market, row in market_checks.items():
        print(
            market,
            "source_equal",
            row["strategy_bytes_equal_to_archived_backtest"],
            "economic_equal",
            row["all_compared_economic_config_equal"],
            "resolved_equal",
            row["all_compared_resolved_strategy_equal"],
            flush=True,
        )


if __name__ == "__main__":
    main()
