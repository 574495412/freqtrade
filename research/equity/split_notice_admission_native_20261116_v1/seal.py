"""Seal the completed technical checkpoint; no financial result is produced."""

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
    verification = json.loads((HERE / "verification.json").read_text())
    assert verification["passed"]
    excluded = {"artifact_manifest.json", "checkpoint_receipt.json"}
    paths = {
        p
        for p in HERE.rglob("*")
        if p.is_file()
        and p.name not in excluded
        and "__pycache__" not in p.parts
        and ".pytest_cache" not in p.parts
    }
    paths.update(
        ROOT / "freqtrade/data" / name
        for name in (
            "equity_split_notices.py",
            "equity_split_notices_provider.py",
            "equity_split_notices.md",
        )
    )
    rows = [{"path": str(p), "bytes": p.stat().st_size, "sha256": sha(p)} for p in sorted(paths)]
    save(
        "artifact_manifest.json",
        {
            "scope": (
                "Post-result source/input technical checkpoint,"
                "not preregistration or account evidence"
            ),
            "files": rows,
        },
    )
    for row in rows:
        assert sha(Path(row["path"])) == row["sha256"]
    receipt = {
        "manifest_sha256": sha(HERE / "artifact_manifest.json"),
        "verified_paths": len(rows),
        "unique_boundary_tests": 38,
        "source_method_witnesses": 4,
        "new_native_stock_accounts": 0,
        "new_factor_analysis": 0,
        "new_portfolio_accounts": 0,
        "higher_comparable_net_return_established": False,
        "goal_complete": False,
    }
    save("checkpoint_receipt.json", receipt)
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
