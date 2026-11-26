"""Verify actual native/source evidence and preserve both attempts in a sealed manifest."""

# ruff: noqa: S101
# Audit invariants are assertions, with optimized Python rejected.
import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import psutil


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if not __debug__:
    raise RuntimeError("Final evidence verification requires assertions")


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def verify_attempts(oldplan, plan):
    attempts = []
    for label, p in [("source_parity_failed_v1", oldplan), ("source_exact_v2", plan)]:
        for c in p["candidates"]:
            directory = Path(p["output_root"]) / (c["name"] + f"_{c['cost_bps']}bps")
            receipt = read(directory / "process_receipt.json")
            assert (
                receipt["returncode"] == 0
                and sha(directory / "result.json") == receipt["result_sha256"]
            )
            assert not (directory / "lossless_cache").exists()
            for name, record in c["source_files"].items():
                assert sha(directory / "strategies" / name) == record["sha256"]
            assert len(list((directory / "native_results").glob("*.zip"))) == 1
            if label == "source_exact_v2":
                independent = read(directory / "independent_account_audit.json")
                assert independent["passed"] and independent["open_trades_at_common_endpoint"] == 0
                assert independent["hypothetical_terminal_force_exits"] == 0
            attempts.append(
                {
                    "version": label,
                    "account": c["name"],
                    "path": str(directory),
                    "returncode": 0,
                    "result_sha256": receipt["result_sha256"],
                }
            )
    return attempts


def main():
    assert not (HERE / "artifact_manifest.json").exists(), "Do not replace sealed evidence"
    current_module = ROOT / "freqtrade/data/ordinal_entropy.py"
    archive = HERE / "implementation_v001/ordinal_entropy.py"
    for path, digest in read(HERE / "implementation_freeze.json").items():
        selected = archive if path == str(current_module) else Path(path)
        assert sha(selected) == digest, path
    for path, digest in read(HERE / "implementation_freeze_v2.json").items():
        assert sha(path) == digest, path
    for filename in [
        "analysis_inputs_freeze.json",
        "audit_extension_freeze.json",
        "endpoint_audit_freeze.json",
    ]:
        for path, digest in read(HERE / filename)["inputs"].items():
            assert sha(path) == digest, path
    # The v2 source audit is a literal plan/output-path revision of the preserved v1 audit.
    for row in read(HERE / "helper_revision_v2.json")["records"]:
        assert sha(HERE / row["original"]) == row["original_sha256"]
        assert sha(HERE / row["new"]) == row["new_sha256"]
        text = (HERE / row["original"]).read_text()
        for old, new in row["literal_replacements"]:
            text = text.replace(old, new)
        assert text == (HERE / row["new"]).read_text()
    assert "5 passed" in (HERE / "tests_v2.log").read_text()
    assert "All checks passed!" in (HERE / "final_lint.log").read_text()
    assert "All checks passed!" in (HERE / "helper_syntax_lint.log").read_text()
    assert read(HERE / "feature_audit_v2.json")["passed"]
    assert read(HERE / "budget_audit_v2.json")["passed"]
    assert read(HERE / "endpoint_audit.json")["passed"]
    result = read(HERE / "results_v2.json")
    assert result["new_native_wallets"] == 4 and len(result["gates"]) == 2
    assert result["new_native_trades"] == 368 and result["new_simulated_fills"] == 1155
    assert (
        result["new_hour_points"] == 58368 and result["maximum_independent_nav_error_usdt"] < 1e-7
    )
    assert not result["financial_gate_passed"] and not any(
        r["financial_gate"] for r in result["gates"]
    )
    plan = read(HERE / "plan_v2.json")
    oldplan = read(HERE / "plan.json")
    # Reuse the unchanged runner's full frozen-engine/input verifier, never start it.
    path = HERE.with_name("trendcore_source_native_20261117_v1") / "native_runner.py"
    spec = importlib.util.spec_from_file_location("frozen_native_input_verifier", path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    for candidate in plan["candidates"]:
        runner.verify(
            {
                **plan,
                "market_snapshot": candidate["market_snapshot"],
                "observer_source": candidate["observer_source"],
            },
            candidate,
        )
    attempts = verify_attempts(oldplan, plan)
    active = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        args = process.info["cmdline"] or []
        if (
            "--plan" in args
            and any(str(HERE) in value for value in args)
            and any("native_runner" in value for value in args)
        ):
            active.append(process.info["pid"])
    assert not active, active
    verification = {
        "passed": True,
        "verified_at": datetime.now(UTC).isoformat(),
        "total_native_financial_attempts": 8,
        "excluded_source_invalid_wallets": 4,
        "source_exact_complete_wallets": 4,
        "source_exact_trades": 368,
        "source_exact_simulated_fills": 1155,
        "source_exact_hour_points": 58368,
        "source_exact_tests": 5,
        "source_rules_or_local_thresholds_retuned": False,
        "full_native_trace_source_gate_audit": True,
        "independent_nav_maximum_error_usdt": result["maximum_independent_nav_error_usdt"],
        "official_core_files_verified": len(read(plan["engine_manifest"]["path"])["files"]),
        "official_engine_commit": plan["engine_commit"],
        "conditional_lower_cost_improvement": True,
        "improvement_under_both_costs": False,
        "financial_gate_passed": False,
        "new_qualified_independent_alpha": 0,
        "independent_oos": False,
        "historical_venue_rules_certified": False,
        "orders_enabled": False,
        "services_changed": False,
        "goal_complete": False,
        "attempts": attempts,
    }
    save(HERE / "verification.json", verification)
    paths = [
        p
        for p in HERE.rglob("*")
        if p.is_file()
        and p.name != "artifact_manifest.json"
        and "__pycache__" not in p.parts
        and ".pytest_cache" not in p.parts
    ]
    paths += [p for p in Path(oldplan["output_root"]).rglob("*") if p.is_file()]
    paths += [current_module]
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "scope": (
            "Both native attempt sets and source/analysis evidence; no other research data copied"
        ),
        "files": [
            {"path": str(p), "bytes": p.stat().st_size, "sha256": sha(p)}
            for p in sorted(set(paths))
        ],
    }
    save(HERE / "artifact_manifest.json", manifest)
    for row in read(HERE / "artifact_manifest.json")["files"]:
        assert sha(row["path"]) == row["sha256"], row["path"]
    print(
        json.dumps(
            {
                "passed": True,
                "total_native_attempts": 8,
                "source_exact_wallets": 4,
                "financial_gate_passed": False,
                "sealed_files": len(manifest["files"]),
                "manifest_sha256": sha(HERE / "artifact_manifest.json"),
            }
        )
    )


if __name__ == "__main__":
    main()
