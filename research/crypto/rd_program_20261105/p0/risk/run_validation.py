"""Run synthetic RD1 tests and write a truthful, small evidence receipt."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import platform
import unittest

import test_rd1
from rd1 import PLAN, load_contract


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed.append(test.id())


def main():
    root = Path(__file__).resolve().parent
    plan, contract_hash = load_contract()
    started = datetime.now(timezone.utc).isoformat()
    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=2, resultclass=RecordedResult).run(
        unittest.defaultTestLoader.loadTestsFromModule(test_rd1))
    files = ["rd1.py", "test_rd1.py", "README.md", "run_validation.py"]
    summary = {
        "schema_version": 1,
        "work_package": "P0_RISK_MODULE",
        "status": "PASS_OFFLINE_ONLY" if result.wasSuccessful() else "FAIL",
        "evidence_level": "A_source_unit_and_fault_injection_synthetic_only",
        "started_utc": started,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "command": "python3 research/crypto/rd_program_20261105/p0/risk/run_validation.py",
        "tests_run": result.testsRun,
        "successes": result.passed,
        "failures": [{"test": t.id(), "trace": trace} for t, trace in result.failures],
        "errors": [{"test": t.id(), "trace": trace} for t, trace in result.errors],
        "skipped": [{"test": t.id(), "reason": reason} for t, reason in result.skipped],
        "plan_sha256": hashlib.sha256(PLAN.read_bytes()).hexdigest(),
        "freeze_sha256": hashlib.sha256(PLAN.with_name("freeze.json").read_bytes()).hexdigest(),
        "risk_contract_canonical_sha256": contract_hash,
        "source_sha256": {f: hashlib.sha256((root / f).read_bytes()).hexdigest() for f in files},
        "runtime_integrated": False,
        "runtime_activation_authorized": plan["risk_contract"]["runtime_activation_authorized"],
        "order_or_network_calls": 0,
        "financial_backtests": 0,
        "official_diagnostic_invocations": 0,
        "evidence_levels_B_C_D_E_completed": False,
        "real_fill_stoploss_liquidation_or_live_profitability_verified": False,
        "initial_validation_note": "First 35-test development run had one failing negative fixture because it converted float to Decimal before invoking the module; corrected to supply raw float. No economic experiment or failed economic candidate was reclassified.",
        "limitations": [
            "Intent-only; not connected to running bots and does not execute cancellations or exits.",
            "External data provenance and reconciliation truth are not established by synthetic inputs.",
            "Future native adapter requires atomic entry-risk reservation and verified reduce-only/market behavior.",
            "Persistence protocol tested with injected write failure, not every physical power-loss scenario.",
            "Other P0/P1 data/causality/funding/mark readiness gates remain independent."
        ],
    }
    (root / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: summary[k] for k in ("status", "tests_run", "runtime_integrated", "financial_backtests")}))
    if not result.wasSuccessful():
        print(output.getvalue())
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
