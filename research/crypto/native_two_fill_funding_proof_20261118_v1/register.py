"""Freeze detached native-object funding fixtures before executing their code."""

# ruff: noqa: E501 - fixed protocol prose preserves exact reviewable strings
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).absolute().parent
VENDOR = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")
MANIFEST = VENDOR.parent / "FREQTRADE_SOURCE.json"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    assert not (HERE / "freeze.json").exists()
    assert not (HERE / "validation.json").exists()
    assert not (HERE / "failure.json").exists()
    protocol = {
        "registered_at": datetime.now(UTC).isoformat(),
        "scope": "Object-level mechanical funding proof, not a full native backtest/matching trajectory",
        "vendor_root": str(VENDOR),
        "vendor_commit": "c051e9065c5b677f03817dd1f83cb980af1ed684",
        "vendor_manifest": {"path": str(MANIFEST), "sha256": sha(MANIFEST)},
        "official_vendor_files_must_match": 595,
        "origin_UTC": "2025-02-01T00:00:00Z",
        "quantity": 2.0,
        "fee": 0.0015,
        "leverage": 1.0,
        "funding": [
            {"minute": 0, "rate": 0.01, "mark": 100.0, "manual_unit": 1.0},
            {"minute": 60, "rate": -0.02, "mark": 100.0, "manual_unit": -2.0},
            {"minute": 120, "rate": 0.03, "mark": 100.0, "manual_unit": 3.0},
            {"minute": 240, "rate": 0.02, "mark": 200.0, "manual_unit": 4.0},
        ],
        "cases": [
            {
                "name": "entry_funding_slot",
                "entry_minute": 0,
                "exit_minute": 125,
                "manual_long_terminal": -4.0,
            },
            {
                "name": "exit_funding_slot",
                "entry_minute": 30,
                "exit_minute": 120,
                "manual_long_terminal": -2.0,
            },
            {
                "name": "entry_equals_exit_funding_slot",
                "entry_minute": 60,
                "exit_minute": 60,
                "manual_long_terminal": 4.0,
            },
            {
                "name": "no_funding_slot",
                "entry_minute": 5,
                "exit_minute": 55,
                "manual_long_terminal": 0.0,
            },
            {
                "name": "five_minute_after_hour_exit",
                "entry_minute": 60,
                "exit_minute": 125,
                "manual_long_terminal": -2.0,
            },
            {
                "name": "signed_rates_both_boundaries",
                "entry_minute": 0,
                "exit_minute": 240,
                "manual_long_terminal": -12.0,
            },
            {
                "name": "force_last_base_after_full_detail",
                "entry_minute": 60,
                "exit_minute": 240,
                "force_tail": True,
                "manual_long_terminal": -10.0,
            },
            {
                "name": "entry_equals_exit_no_slot",
                "entry_minute": 30,
                "exit_minute": 30,
                "manual_long_terminal": 0.0,
            },
        ],
        "directions": ["long", "short"],
        "expected_cases": 16,
        "native_order_sequence": [
            "Hourly observer snapshot before the current5m event",
            "Detached LocalTrade initialized with amount0; native adjust_stop_loss before entry order",
            "Append real market entry Order; Order.close_bt_order (includes entry recalc); real _run_funding_fees(force=True); final LocalTrade.recalc_trade_from_orders",
            "Same-clock and each later5m _check_trade_exit funding step: real _run_funding_fees(force=False)",
            "Append full exit Order; close_bt_order transfers running to order; _run(force=True); set close_date; trade.close calls recalc(is_closing=True)",
            "Force-tail fixture only: complete5m loop through C+55min then close using final base clock C",
        ],
        "funding_data_owner": "Real Exchange.combine_funding_and_mark and calculate_funding_fees; Exchange constructor is forbidden",
        "method_binding": "Real Backtesting._run_funding_fees bound to SimpleNamespace with only its four required attributes; no Backtesting object is created",
        "oracle": "Independent manual units and terminal expectations; sign*q*sum(E<=F<=C), snapshots sign*q*sum(E<=F<T and F<=C)",
        "numerical_tolerance_absolute_USDT": 1e-12,
        "omitted_nonfunding_actions": "No wallet update, matching, liquidation updates, strategy callbacks or trade-global registration. The frozen H&S policy has no funding-mutating callbacks. This proof does not certify omitted actions or market execution.",
        "forbidden": [
            "Backtesting constructor",
            "Backtesting.start",
            "Wallets constructor",
            "Exchange constructor",
            "network connects",
            "financial account run",
            "market data",
        ],
        "runtime_guards": "Patch forbidden entry points to raise within fixture phase; verify native-global references and collection sizes unchanged; peak RSS<1GiB",
        "failure_policy": "Retain original frozen code, protocol and failed log; a correction requires separate version",
        "synthetic_executed_before_freeze": False,
        "financial_accounts_run": 0,
        "production_eligible": False,
    }
    (HERE / "protocol.json").write_text(json.dumps(protocol, indent=2) + "\n")
    owners = [
        VENDOR / relative
        for relative in (
            "freqtrade/persistence/trade_model.py",
            "freqtrade/optimize/backtesting.py",
            "freqtrade/exchange/exchange.py",
            "freqtrade/data/btanalysis/bt_fileutils.py",
        )
    ]
    files = [
        HERE / "prove_objects.py",
        HERE / "register.py",
        HERE / "protocol.json",
        MANIFEST,
        *owners,
    ]
    freeze = {
        "frozen_at": datetime.now(UTC).isoformat(),
        "executed_before_freeze": False,
        "files": {str(p): sha(p) for p in files},
    }
    (HERE / "freeze.json").write_text(json.dumps(freeze, indent=2) + "\n")
    print(
        json.dumps(
            {
                "frozen_files": len(files),
                "freeze_sha256": sha(HERE / "freeze.json"),
                "native_objects_executed": False,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
