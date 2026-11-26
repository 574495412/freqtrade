"""Additive regression for scalar timestamp storage units; no market data."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from independent_account_audit_v2 import HOUR, ms, sha


HERE = Path(__file__).absolute().parent


def main():
    frozen = json.loads((HERE / "independent_audit_unit_freeze_v3.json").read_text())
    for path, digest in frozen["files"].items():
        assert sha(path) == digest
    expected = 1690848000000
    origin = pd.Timestamp("2023-08-01T00:00:00Z")
    fixtures = [(f"pandas_{unit}", origin.as_unit(unit)) for unit in ("ns", "us", "ms")]
    fixtures.append(("stdlib_datetime_UTC", datetime(2023, 8, 1, tzinfo=UTC)))
    cases = []
    for name, value in fixtures:
        assert ms(value) == expected
        assert ms(pd.Timestamp(value) + pd.Timedelta(hours=1)) == expected + HOUR
        assert ms(pd.Timestamp(value) + pd.Timedelta(minutes=5)) == expected + 300000
        cases.append(
            {"name": name, "epoch_ms": ms(value), "hour_step_ms": HOUR, "detail_step_ms": 300000}
        )
    result = {
        "passed": True,
        "storage_unit_cases": len(cases),
        "integer_clock_assertions": 12,
        "cases": cases,
        "native_accounts_run": 0,
        "financial_outputs_read": False,
        "tested_code_sha256": sha(HERE / "independent_account_audit_v2.py"),
        "freeze_sha256": sha(HERE / "independent_audit_unit_freeze_v3.json"),
    }
    (HERE / "independent_audit_units_validation_v3.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
