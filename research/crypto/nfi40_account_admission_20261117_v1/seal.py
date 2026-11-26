"""Seal these read-only reviews without replacing an existing manifest."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent
FUNDING = HERE.parent / "funding_slot_provenance_native_20261116_v1"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root):
    return [
        {"path": str(path), "sha256": digest(path), "bytes": path.stat().st_size}
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path != root / "artifact_manifest.json"
        and "__pycache__" not in path.parts
        and ".pytest_cache" not in path.parts
    ]


def seal(root):
    manifest = root / "artifact_manifest.json"
    files = inventory(root)
    if manifest.exists():
        assert json.loads(manifest.read_text())["files"] == files, root
    else:
        with manifest.open("x") as handle:
            json.dump(
                {
                    "sealed_at": datetime.now(UTC).isoformat(),
                    "scope": "Read-only old-account and input review; no new financial account",
                    "files": files,
                    "orders_enabled": False,
                    "goal_complete": False,
                },
                handle,
                indent=2,
            )
            handle.write("\n")
    assert json.loads(manifest.read_text())["files"] == inventory(root), root
    print(f"Verified {root.name}: {len(files)} files; manifest SHA256 {digest(manifest)}")


if __name__ == "__main__":
    seal(FUNDING)
    seal(HERE)
