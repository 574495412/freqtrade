"""Fail-closed gates for a future technical replay; never starts an account."""

import fcntl
import hashlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path


HERE = Path(__file__).resolve().parent


def sha(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            result.update(chunk)
    return result.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def checked(record):
    path = Path(record["path"])
    if path.is_symlink() or sha(path) != record["sha256"]:
        raise ValueError(f"Frozen input changed: {path}")
    return path


def release_contract(release, plan_sha, producer_sha):
    if (
        release.get("plan_sha256") != plan_sha
        or release.get("producer_freeze_sha256") != producer_sha
        or release.get("root_reviewed") is not True
        or release.get("technical_native_start_authorized") is not True
        or release.get("existing_controller_replacement_authorized") is not False
    ):
        raise ValueError("Root release does not authorize this exact technical replay")


def predecessor_completed(dependency):
    checked(dependency["plan"])
    complete = read(dependency["execution_complete_path"])
    names = dependency["candidate_names"]
    if complete.get("native_accounts_completed") != len(names) or sorted(
        complete.get("completed", [])
    ) != sorted(names):
        raise ValueError("Predecessor financial queue is incomplete")
    for name in names:
        output = Path(dependency["output_root"]) / name
        process = read(output / "process_receipt.json")
        result_path = output / "result.json"
        if process.get("returncode") != 0 or process.get("result_sha256") != sha(result_path):
            raise ValueError("Predecessor process/result does not reconcile")
        result = read(result_path)
        if result.get("candidate") != name or result.get("orders_enabled") is not False:
            raise ValueError("Predecessor account identity or mode differs")


def require_release():
    if sys.flags.optimize:
        raise ValueError("Assertions must remain enabled for the frozen research code")
    plan_path = HERE / "plan.json"
    plan = read(plan_path)
    frozen = read(HERE / "preparation_freeze.json")
    for path, digest in frozen["files"].items():
        checked({"path": path, "sha256": digest})
    release_path = HERE / "execution_release.json"
    if not release_path.is_file():
        raise ValueError("HELD: root technical replay release is absent")
    # Deliberately absent at preparation stage: the producer must be separately
    # implemented, reviewed and frozen before a root release can admit any start.
    producer_path = HERE / "producer_freeze.json"
    if not producer_path.is_file():
        raise ValueError("HELD: native lifecycle producer is not implemented/frozen")
    producer = read(producer_path)
    if (
        producer.get("plan_sha256") != sha(plan_path)
        or producer.get("implementation_complete") is not True
        or producer.get("before_any_native_start") is not True
        or not producer.get("files")
    ):
        raise ValueError("Producer is not bound to the prepared lifecycle contract")
    runner = checked(producer["native_start_entrypoint"])
    if runner.parent != HERE or str(runner) not in producer["files"]:
        raise ValueError("Future producer entrypoint must be sealed in this new study")
    for path, digest in producer["files"].items():
        checked({"path": path, "sha256": digest})
    release_contract(read(release_path), sha(plan_path), sha(producer_path))
    for dependency in plan["predecessors"]:
        predecessor_completed(dependency)
    return plan


@contextmanager
def shared_worker_admission():
    """A future producer must hold both locks through start, export and cleanup."""
    plan = require_release()
    with (
        Path(plan["shared_financial_lock"]).open("rb") as shared,
        (HERE / "technical_worker.lock").open("a+b") as local,
    ):
        fcntl.flock(shared, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(local, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require_release()  # Recheck predecessor/release state after lock acquisition.
        yield plan


if __name__ == "__main__":
    require_release()
