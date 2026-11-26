"""Seal the implementation before final native orders, retaining debug attempts."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    target = HERE / "implementation_freeze.json"
    if target.exists():
        raise ValueError("Do not overwrite the original final-batch freeze")
    paths = list(HERE.glob("*.py"))
    paths += [
        ROOT / "freqtrade" / p
        for p in [
            "data/option_cash.py",
            "option_wallets.py",
            "optimize/option_fee_execution.py",
            "optimize/option_fixture_lifecycle.py",
        ]
    ]
    paths += [
        HERE / p
        for p in [
            "protocol.json",
            "full_start_protocol.json",
            "technical_amendment.json",
            "ownership.json",
            "source_intake.json",
        ]
    ]
    owner = json.loads((HERE / "ownership.json").read_text())
    for record in owner["current_owner_files"] + owner["native_owner_files"]:
        if sha(record["path"]) != record["sha256"]:
            raise ValueError("Changed owning implementation: " + record["path"])
        paths.append(Path(record["path"]))
    for row in json.loads((HERE / "protocol.json").read_text())["sources"]:
        if sha(row["path"]) != row["sha256"]:
            raise ValueError("Changed original metadata")
        paths.append(Path(row["path"]))
    template = ROOT / "user_data/crypto_quant/copula_fills_native_20261115_v4/fixtures/normal1000"
    paths += [template / p for p in ["config.json", "synthetic_markets_maintenance_fixed.json"]]
    result = {
        "frozen_at": datetime.now(UTC).isoformat(),
        "files": {str(p): sha(p) for p in paths},
        "scope": "Before final synthetic native processes;debug attempts remain separate",
    }
    target.write_text(json.dumps(result, indent=2) + "\n")
    print("Frozen paths", len(result["files"]), flush=True)


if __name__ == "__main__":
    main()
