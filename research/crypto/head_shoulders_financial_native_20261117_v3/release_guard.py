"""Explicit root-reviewed queue release; input, predecessor and single-worker gates."""

import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require_release(plan_path):
    plan_path = Path(plan_path).resolve()
    plan = json.loads(plan_path.read_text())
    release_path = plan_path.parent / "execution_release.json"
    if not release_path.is_file():
        raise ValueError("Financial queue remains held until root plan review and release")
    release = json.loads(release_path.read_text())
    if (
        release.get("plan_sha256") != sha(plan_path)
        or release.get("root_reviewed") is not True
        or release.get("financial_start_authorized") is not True
    ):
        raise ValueError("Release is not bound to this reviewed financial plan")
    record = plan["input_validation"]
    if sha(record["path"]) != record["sha256"]:
        raise ValueError("Frozen source/detail validation changed")
    validated = json.loads(Path(record["path"]).read_text())
    if not validated.get("passed") or validated.get("ordered_pairs") != plan["pairs"]:
        raise ValueError("All original40 source/detail input checks must pass")
    dependency = plan["nfi500_dependency"]
    if sha(dependency["plan"]["path"]) != dependency["plan"]["sha256"]:
        raise ValueError("Original actual500 NFI plan changed")
    complete = json.loads(Path(dependency["execution_complete_path"]).read_text())
    if complete.get("native_accounts_completed") != 2 or set(complete.get("completed", [])) != set(
        dependency["candidate_names"]
    ):
        raise ValueError("NFI500 predecessor has not reached the required completed state")
    for name in dependency["candidate_names"]:
        output = Path(dependency["output_root"]) / name
        receipt = json.loads((output / "process_receipt.json").read_text())
        if receipt.get("returncode") != 0 or receipt.get("result_sha256") != sha(
            output / "result.json"
        ):
            raise ValueError("NFI500 predecessor process/results are not complete")
    return release
