"""Read actual native ZIPs and independently audit fixture cash and clocks."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path
from zipfile import ZipFile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/suspension_native_20261117_v1"


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def archived(folder):
    archives = list((folder / "native_results").glob("*.zip"))
    assert len(archives) == 1
    with ZipFile(archives[0]) as z:
        assert z.testzip() is None
        payloads = [json.loads(z.read(n)) for n in z.namelist() if n.endswith(".json")]
    return next(v["strategy"]["FixtureOperations"] for v in payloads if "strategy" in v), archives[
        0
    ]


def main():
    plan = read(HERE / "fixture_plan.json")
    amendment = read(HERE / "serialization_amendment.json")
    for path, digest in plan["source_hashes"].items():
        updated = amendment["source_changes"].get(path)
        assert sha(path) == (updated["after"] if updated else digest), path
    for case in plan["cases"]:
        for path, digest in case["inputs"].items():
            assert sha(path) == digest, path
    evidence = []
    verified_paths = {}
    halt, resume = 1777442400000, 1777442460000
    for case, attempt, first_exit in (
        ("continue", "audited_v2", halt),
        ("stop", "audited", halt + 120000),
    ):
        folder = OUT / "runs" / case / attempt
        result, producer = read(folder / "result.json"), read(folder / "producer_receipt.json")
        assert producer["runner_sha256"] == sha(HERE / "run_fixture.py")
        assert producer["save_helper_sha256"] == sha(HERE / "prepare_fixture.py")
        assert producer["native_extension_sha256"] == sha(
            ROOT / "freqtrade/optimize/order_operations.py"
        )
        assert producer["plan_sha256"] == sha(HERE / "fixture_plan.json")
        stats, archive = archived(folder)
        assert result["native_start_completed"] and result["open_native_trades"] == 0
        assert len(stats["trades"]) == 2
        ledger, errors = D(0), []
        for index, trade in enumerate(stats["trades"]):
            entry = next(o for o in trade["orders"] if o["ft_is_entry"])
            exit_ = next(o for o in trade["orders"] if not o["ft_is_entry"])
            assert trade["is_short"] is False and trade["leverage"] == 1
            qty = D(str(entry["amount"]))
            assert qty == D(str(exit_["amount"]))
            purchase, sale = qty * D(str(entry["safe_price"])), qty * D(str(exit_["safe_price"]))
            funding = -qty * D(100) * D("0.001") if index == 0 else D(0)
            assert D(str(trade["funding_fees"])) == funding
            pnl = (
                sale
                - purchase
                - purchase * D(str(trade["fee_open"]))
                - sale * D(str(trade["fee_close"]))
                + funding
            )
            error = abs(pnl - D(str(trade["profit_abs"])))
            assert error < D("0.0000001")
            errors.append(str(error))
            ledger += pnl
            assert exit_["order_filled_timestamp"] == (first_exit if index == 0 else halt + 600000)
            if index == 0:
                assert D(str(exit_["safe_price"])) == D("103.005")
                assert D("103.005") % D("0.01") != 0  # Grandfathered old order.
            else:
                assert entry["order_filled_timestamp"] == resume
                assert D(str(entry["safe_price"])) == D("100.01")
        assert abs(D(str(result["native_wallet_final"])) - D(1000) - ledger) < D("0.0000001")
        assert abs(D(str(stats["final_balance"])) - D(1000) - ledger) < D("0.0000001")
        trace = read(folder / "operation_trace.json")
        assert any(r["action"] == "new_entry" and r["pair"].startswith("FAKETWO") for r in trace)
        assert all(
            halt <= int(datetime.fromisoformat(r["at"]).timestamp() * 1000) < resume for r in trace
        )
        evidence.append(
            {
                "case": case,
                "actual_archive": str(archive),
                "archive_sha256": sha(archive),
                "native_completed": True,
                "first_exit_ms": first_exit,
                "new_entry_ms": resume,
                "independent_cash_error": errors,
                "funding_retained": True,
                "synthetic_only": True,
            }
        )
        for path in folder.rglob("*"):
            if path.is_file():
                verified_paths[str(path)] = sha(path)
    for case, message in (
        ("unknown", "Unknown pending matching policy"),
        ("missing_detail", "Missing genuine1m input"),
    ):
        folder = OUT / "runs" / case / "audited"
        failure = read(folder / "failure.json")
        assert message in failure["error"] and failure["native_start_completed"] is False
        assert not list((folder / "native_results").glob("*.zip"))
        evidence.append({"case": case, "expected_rejected": True, "error": failure["error"]})
        for path in folder.rglob("*"):
            if path.is_file():
                verified_paths[str(path)] = sha(path)
    initial, first_archive = archived(OUT / "runs/continue")
    repeated, _ = archived(OUT / "runs/continue/audited_v2")
    assert initial["trades"] == repeated["trades"]
    assert read(HERE / "source_audit.json")["passed"]
    source_manifest = read(ROOT / "deploy/upstream-source.json")
    for rel, digest in source_manifest["files"].items():
        assert sha(ROOT.parent / "CZ-Agent/vendor/freqtrade" / rel) == digest
    output = {
        "verified_at": datetime.now(UTC).isoformat(),
        "passed": True,
        "audited_native_start_completed": 2,
        "expected_native_start_rejections": 2,
        "original_engine_completed_aux_export_failed": 1,
        "original_archive": str(first_archive),
        "original_runner_sha_missing_retained": True,
        "original_and_audited_continue_trades_identical": True,
        "prestart_nameerror_attempt_retained": True,
        "official_vendor_files_checked": len(source_manifest["files"]),
        "evidence": evidence,
        "verified_artifact_hashes": verified_paths,
        "new_historical_strategy_accounts": 0,
        "orders_enabled": False,
        "production_eligible": False,
        "goal_complete": False,
    }
    (HERE / "verification.json").write_text(json.dumps(output, indent=2) + "\n")
    print(
        "Verified2complete native fixtures,2expected rejections,actual ZIPs and cash/funding;"
        "0historical alpha accounts"
    )


if __name__ == "__main__":
    main()
