"""Seal new producer inputs before detached probes; never releases or starts anything."""

import json
from datetime import UTC, datetime

from .admission import HERE, checked, read, sha


def run():
    if (HERE / "execution_release.json").exists():
        raise ValueError("Preparation must not create or reuse a release")
    destination = HERE / "producer_freeze.json"
    if destination.exists():
        raise ValueError("Producer freeze already exists; preserve it")
    preparation = read(HERE / "artifact_manifest.json")
    # Existing manifest schema is inspected rather than rewriting its contents.
    for path, record in preparation["files"].items():
        checked(
            {
                "path": str(HERE / path) if not path.startswith("/") else path,
                "sha256": record["sha256"],
            }
        )
    protocol = read(HERE / "producer_release_protocol.json")
    files = {str(HERE / name): sha(HERE / name) for name in protocol["new_files"]}
    plan = read(HERE / "plan.json")
    for record in [v["storage_source"] for v in plan["variants"]]:
        checked(record)
        files[record["path"]] = record["sha256"]
    freeze = {
        "schema": "nfi40-lifecycle-producer-freeze.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "plan_sha256": sha(HERE / "plan.json"),
        "implementation_complete": True,
        "implementation_scope": "actual fixed short NFI producer only",
        "supplemental_synthetic_native_producer_implemented": False,
        "before_any_native_start": True,
        "before_detached_object_probe": True,
        "preparation_manifest_sha256": sha(HERE / "artifact_manifest.json"),
        "native_start_entrypoint": {
            "path": str(HERE / "native_producer.py"),
            "sha256": sha(HERE / "native_producer.py"),
        },
        "files": files,
        "execution_authorized": False,
        "native_accounts_started": 0,
        "technical_recovery_branch_implemented": False,
    }
    with destination.open("x") as stream:
        json.dump(freeze, stream, indent=2)
        stream.write("\n")
    return {"path": str(destination), "sha256": sha(destination)}


if __name__ == "__main__":
    print(json.dumps(run()))
