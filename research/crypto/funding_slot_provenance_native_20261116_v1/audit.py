"""Original source bytes and observed funding rows; no rate imputation."""

import csv
import hashlib
import io
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile


HERE = Path(__file__).absolute().parent
SLOT = 1782273600000


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    receipts = json.loads((HERE / "source_receipts.json").read_text())
    for row in receipts:
        assert row["status"] == 200 and sha(row["path"]) == row["sha256"]
    outcomes = []
    for symbol in ["AXSUSDT", "TRBUSDT"]:
        base = HERE / "sources"
        archive = base / (symbol + "_archive.zip")
        checksum = (base / (symbol + "_archive.zip.CHECKSUM")).read_text().split()[0]
        assert sha(archive) == checksum
        with ZipFile(archive) as z:
            assert z.testzip() is None and len(z.namelist()) == 1
            raw = z.read(z.namelist()[0])
        assert raw == (base / (symbol + "_archive.csv")).read_bytes()
        rows = list(csv.DictReader(io.StringIO(raw.decode())))
        assert len(rows) == 179
        assert set(rows[0]) == {"calc_time", "funding_interval_hours", "last_funding_rate"}
        times = [int(r["calc_time"]) for r in rows]
        assert times == sorted(set(times))
        rates = {int(r["calc_time"]): Decimal(r["last_funding_rate"]) for r in rows}
        assert all(rate.is_finite() for rate in rates.values())
        rest = json.loads((base / (symbol + "_REST.json")).read_text())
        linked = json.loads((base / (symbol + "_connector.json")).read_text())
        connected = linked["response"]["result"]
        assert connected == rest
        observed = []
        for r in rest:
            when = int(r["fundingTime"])
            assert when in rates and rates[when] == Decimal(r["fundingRate"])
            assert r["symbol"] == symbol
            observed.append(when)
        assert not any(SLOT <= t < SLOT + 3600000 for t in times)
        assert not any(SLOT <= t < SLOT + 3600000 for t in observed)
        day = [
            r
            for r in rows
            if "2026-06-24"
            == datetime.fromtimestamp(int(r["calc_time"]) / 1000, UTC).strftime("%Y-%m-%d")
        ]
        outcomes.append(
            {
                "symbol": symbol,
                "archive_rows": len(rows),
                "rest_rows": len(rest),
                "published_zip_checksum_passed": True,
                "rest_connector_exact": True,
                "archive_rest_exact": True,
                "june24_rows": day,
                "04UTC_hour_observed": False,
                "missing_expected_slot_rate": None,
                "status": "UNKNOWN",
                "scope": (
                    "Same exchange history/archive authority, not authenticated customer settlement"
                ),
            }
        )
    result = {
        "audited_at": datetime.now(UTC).isoformat(),
        "passed_source_checks": True,
        "slots": outcomes,
        "source_receipts_checked": len(receipts),
        "dated_schedule_change_proven": False,
        "funding_rate_imputed": False,
        "new_financial_accounts": 0,
        "orders_enabled": False,
        "historical_first_seen_certified": False,
        "goal_complete": False,
    }
    (HERE / "source_audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print("Checked2ZIP+published checksums,archive/REST/connector parity;2slots remainUNKNOWN")


if __name__ == "__main__":
    main()
