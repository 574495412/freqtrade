"""Independent raw-native fill/cash audit and exact source/version readback."""

import ast
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path
from zipfile import ZipFile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/amount_rules_native_20261117_v1"


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stats(folder):
    paths = list((folder / "native_results").glob("*.zip"))
    assert len(paths) == 1
    with ZipFile(paths[0]) as z:
        assert z.testzip() is None
        payloads = [json.loads(z.read(n)) for n in z.namelist() if n.endswith(".json")]
    data = next(p["strategy"] for p in payloads if "strategy" in p)
    assert len(data) == 1
    return next(iter(data.values())), paths[0]


def methods(path):
    tree = ast.parse(Path(path).read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Backtesting")
    return {
        n.name: ast.dump(n, include_attributes=False)
        for n in cls.body
        if isinstance(n, ast.FunctionDef)
    }


def main():
    plan = read(HERE / "fixture_plan.json")
    parity = read(HERE / "runner_style_parity.json")
    producer = HERE / "producer_run_native.py.txt"
    assert sha(producer) == parity["actual_executed_sha256"]
    assert sha(HERE / "run_native.py") == parity["formatted_current_sha256"]
    assert ast.dump(ast.parse(producer.read_text()), include_attributes=False) == ast.dump(
        ast.parse((HERE / "run_native.py").read_text()), include_attributes=False
    )
    for path, digest in plan["source_hashes"].items():
        assert sha(producer if Path(path).name == "run_native.py" else path) == digest, path
    for case in plan["cases"]:
        for path, digest in case["inputs"].items():
            assert sha(path) == digest, path
    before = methods(HERE / "before/freqtrade/optimize/backtesting.py")
    after = methods(ROOT / "freqtrade/optimize/backtesting.py")
    added = {
        "get_entry_amount_precision",
        "get_exit_amount_precision",
        "get_order_stake_limits",
        "validate_order_amount",
        "update_trade_amount_precision",
        "can_reduce_position",
    }
    changed = {
        "_enter_trade",
        "_exit_trade",
        "get_valid_entry_price_and_stake",
        "_check_adjust_trade_for_candle",
    }
    assert set(after) - set(before) == added and not set(before) - set(after)
    assert all(before[k] == after[k] for k in set(before) - changed)
    evidence, artifacts = [], {}
    for name in ("fine_DCA", "notional_fall", "post_round"):
        folder = OUT / "runs" / name
        result = read(folder / "result.json")
        assert read(folder / "producer_receipt.json")["source_hashes"] == plan["source_hashes"]
        native, archive = stats(folder)
        assert result["native_start_completed"] and result["open_trades"] == 0
        pnl, errors = D(0), []
        if name == "post_round":
            assert not native["trades"]
            trace = read(folder / "amount_trace.json")
            assert trace and all(not r["accepted"] for r in trace)
            assert all(r["reason"] == "minimum_notional_after_rounding" for r in trace)
        else:
            assert len(native["trades"]) == 1
            trade = native["trades"][0]
            orders = trade["orders"]
            quantities = [D(str(o["amount"])) for o in orders]
            assert quantities == (
                [D(1), D("0.6"), D("1.2"), D("0.4")] if name == "fine_DCA" else [D("0.6"), D("0.6")]
            )
            buy = sell = balance = D(0)
            fee_ms = int(datetime(2026, 4, 29, 6, tzinfo=UTC).timestamp() * 1000)
            qty_before_fee = D(0)
            for order in orders:
                quantity, price = D(str(order["amount"])), D(str(order["safe_price"]))
                when = order["order_filled_timestamp"]
                assert price == 100
                if order["ft_is_entry"]:
                    buy += quantity * price
                    balance += quantity
                    if when <= fee_ms:
                        qty_before_fee += quantity
                else:
                    sell += quantity * price
                    balance -= quantity
                    if when < fee_ms:
                        qty_before_fee -= quantity
                assert balance >= 0
            assert balance == 0
            # Frozen native Binance funding convention includes an entry at the
            # full-hour boundary. This is model parity,not venue certification.
            funding = -qty_before_fee * D(100) * D("0.001")
            assert funding == D(str(trade["funding_fees"]))
            pnl = (
                sell
                - buy
                - buy * D(str(trade["fee_open"]))
                - sell * D(str(trade["fee_close"]))
                + funding
            )
            errors.append(abs(pnl - D(str(trade["profit_abs"]))))
            if name == "fine_DCA":
                assert [o["order_filled_timestamp"] for o in orders] == [
                    1777441800000,
                    1777442460000,
                    1777442700000,
                    1777443000000,
                ]
                assert quantities[-1] * D(100) < 50  # Explicit reduce-only exemption.
            else:
                assert trade["open_timestamp"] == fee_ms
        errors.append(abs(D(str(result["native_wallet_final"])) - D(1000) - pnl))
        errors.append(abs(D(str(native["final_balance"])) - D(1000) - pnl))
        assert all(e < D("0.0000001") for e in errors)
        evidence.append(
            {
                "case": name,
                "native_zip": str(archive),
                "sha256": sha(archive),
                "independent_cash_errors": [str(e) for e in errors],
                "synthetic_only": True,
            }
        )
        for path in folder.rglob("*"):
            if path.is_file():
                artifacts[str(path)] = sha(path)
    for name in ("coarse_dust", "unknown"):
        folder = OUT / "runs" / name
        failure = read(folder / "failure.json")
        assert not failure["native_start_completed"]
        assert not list((folder / "native_results").glob("*.zip"))
        if name == "coarse_dust":
            assert (
                failure["native_start_attempted"]
                and "Unsupported dated close amount" in failure["error"]
            )
            assert [D(str(t["amount"])) for t in failure["open_trades"]] == [D("0.6")]
        else:
            assert (
                not failure["native_start_attempted"]
                and "Incomplete known amount" in failure["error"]
            )
        evidence.append({"case": name, "expected_rejected": True, "error": failure["error"]})
        for path in folder.rglob("*"):
            if path.is_file():
                artifacts[str(path)] = sha(path)
    for name in ("continue", "stop"):
        folder = OUT / "operation_regression" / name
        result = read(folder / "result.json")
        current_stats, archive = stats(folder)
        prior_attempt = "audited_v2" if name == "continue" else "audited"
        prior_folder = (
            ROOT
            / "user_data/crypto_quant/suspension_native_20261117_v1/runs"
            / name
            / prior_attempt
        )
        old_stats, old_archive = stats(prior_folder)
        assert current_stats["trades"] == old_stats["trades"]
        assert current_stats["final_balance"] == old_stats["final_balance"]
        assert (
            result["native_start_completed"]
            and result["trades_identical"]
            and result["wallet_identical"]
        )
        assert result["open_trades"] == 0
        evidence.append(
            {
                "case": "legacy_" + name,
                "native_zip": str(archive),
                "sha256": sha(archive),
                "default_native_result_identical": True,
                "prior_actual_zip": str(old_archive),
                "prior_actual_zip_sha256": sha(old_archive),
            }
        )
        for path in folder.rglob("*"):
            if path.is_file():
                artifacts[str(path)] = sha(path)
    manifest = read(ROOT / "deploy/upstream-source.json")
    for rel, digest in manifest["files"].items():
        assert sha(ROOT.parent / "CZ-Agent/vendor/freqtrade" / rel) == digest
    assert "33 passed" in (HERE / "tests_final.log").read_text()
    result = {
        "verified_at": datetime.now(UTC).isoformat(),
        "passed": True,
        "new_synthetic_native_completed": 3,
        "new_expected_rejections": 2,
        "native_operation_regressions_identical": 2,
        "actual_native_ZIPs_verified": 5,
        "unit_and_existing_price_regression_cases": 33,
        "official_vendor_files_unchanged": len(manifest["files"]),
        "native_methods_added": sorted(added),
        "native_methods_changed": sorted(changed),
        "preexisting_other_methods_AST_unchanged": len(set(before) - changed),
        "evidence": evidence,
        "native_artifact_hashes": artifacts,
        "new_historical_strategy_accounts": 0,
        "corrected_NFI_returns": None,
        "full40_historical_amount_rules_admitted": False,
        "orders_enabled": False,
        "production_eligible": False,
        "goal_complete": False,
    }
    (HERE / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        "Verified5nativeZIPs,2expected rejects,33tests and independent cash;"
        "0historical alpha accounts"
    )


if __name__ == "__main__":
    main()
