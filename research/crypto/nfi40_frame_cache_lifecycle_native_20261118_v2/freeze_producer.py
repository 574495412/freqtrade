"""Freeze the v2 technical correction before object probes; no release or account."""

import json
from datetime import UTC, datetime
from pathlib import Path

from .admission import HERE, checked, read, sha


def run():
    destination = HERE / "producer_freeze.json"
    if destination.exists() or (HERE / "execution_release.json").exists():
        raise ValueError("Cannot overwrite freeze or reuse a release")
    protocol = read(HERE / "producer_release_protocol.json")
    old = HERE.parent / "nfi40_frame_cache_lifecycle_native_20261117_v1"
    for name in protocol["unchanged_files"]:
        if (HERE / name).read_bytes() != (old / name).read_bytes():
            raise ValueError("Unchanged economic/gate source differs: " + name)
    for record in protocol["lineage"].values():
        manifest = read(checked(record))
        for path, item in manifest["files"].items():
            checked({"path": path, "sha256": item["sha256"]})
    files = {str(HERE / name): sha(HERE / name) for name in protocol["new_files"]}
    for record in protocol["lineage"].values():
        files[record["path"]] = record["sha256"]
    plan = read(HERE / "plan.json")
    for variant in plan["variants"]:
        checked(variant["storage_source"])
        files[variant["storage_source"]["path"]] = variant["storage_source"]["sha256"]
    result = {
        "schema": "nfi40-lifecycle-producer-freeze.v2",
        "created_at": datetime.now(UTC).isoformat(),
        "plan_sha256": sha(HERE / "plan.json"),
        "economic_plan_unchanged": True,
        "implementation_complete": True,
        "implementation_scope": "Fixed short native producer with corrected terminal resource and observed cache-path gates",
        "before_any_native_start": True,
        "before_object_probes": True,
        "native_start_entrypoint": {
            "path": str(HERE / "native_producer.py"),
            "sha256": sha(HERE / "native_producer.py"),
        },
        "files": files,
        "execution_authorized": False,
        "native_accounts_started": 0,
        "technical_recovery_branch_implemented": False,
        "supplemental_synthetic_native_producer_implemented": False,
        "complete_cache_lifecycle_certified": False,
    }
    with destination.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    return {"path": str(destination), "sha256": sha(destination)}


if __name__ == "__main__":
    print(json.dumps(run()))
