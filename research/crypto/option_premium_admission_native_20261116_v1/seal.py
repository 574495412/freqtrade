"""Seal the completed source/input checkpoint;not financial preregistration."""

import hashlib
import json
from pathlib import Path


HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    assert json.loads((HERE / "verification.json").read_text())["passed"]
    paths = {
        p
        for p in HERE.rglob("*")
        if p.is_file()
        and p.name not in {"artifact_manifest.json", "checkpoint_receipt.json"}
        and "__pycache__" not in p.parts
        and ".pytest_cache" not in p.parts
    }
    paths.update(
        ROOT / "freqtrade" / p
        for p in (
            "data/inverse_option_contracts.py",
            "data/inverse_option_contracts.md",
            "data/history/inverse_options.py",
            "optimize/option_backtesting.py",
        )
    )
    rows = [{"path": str(p), "bytes": p.stat().st_size, "sha256": sha(p)} for p in sorted(paths)]
    save(
        "artifact_manifest.json",
        {
            "scope": "Post-result technical source/input checkpoint,not a financial result",
            "files": rows,
        },
    )
    for row in rows:
        assert sha(Path(row["path"])) == row["sha256"]
    receipt = {
        "manifest_sha256": sha(HERE / "artifact_manifest.json"),
        "verified_paths": len(rows),
        "unique_boundary_tests": 55,
        "native_historical_chart_rows": 25,
        "new_financial_accounts": 0,
        "new_portfolio_accounts": 0,
        "goal_complete": False,
    }
    save("checkpoint_receipt.json", receipt)
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
