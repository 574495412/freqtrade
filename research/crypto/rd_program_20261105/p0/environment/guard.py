"""Read-only source/data guards for isolated RD research, never a trading launcher."""
from __future__ import annotations

import argparse
import hashlib
import importlib.abc
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
PROGRAM = HERE.parents[1]
ENGINE_COMMIT = "c051e9065c5b677f03817dd1f83cb980af1ed684"
ALLOWED_UNUSED_EXTENSIONS = {
    f"freqtrade/research/{name}.py" for name in
    ("__init__", "__main__", "data", "engine", "strategies", "tradingagents_worker", "validation", "workflow")
}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def local_path(value: str) -> Path:
    """Accept repository files, excluding private runtime state and secret files."""
    raw = Path(value)
    path = raw if raw.is_absolute() else ROOT / raw
    if path.is_symlink():
        raise ValueError("Symlink input rejected")
    resolved = path.resolve()
    relative = resolved.relative_to(ROOT)
    if relative.parts[:2] == ("user_data", "deployment") or any(
        part.startswith(".env") for part in relative.parts
    ):
        raise ValueError("Private runtime input is outside research scope")
    return resolved


def checked(record: dict) -> Path:
    path = local_path(record["path"])
    if not path.is_file() or sha(path) != record["sha256"]:
        raise ValueError(f"Missing or changed registered input: {record['path']}")
    return path


def read_json(relative: str) -> dict:
    return json.loads(local_path(relative).read_text())


def verify_program() -> None:
    freeze = json.loads((PROGRAM / "freeze.json").read_text())
    for group in ("files", "deployment_locks"):
        for name, expected in freeze[group].items():
            checked({"path": name, "sha256": expected})


def verify_environment(snapshot: dict | None = None) -> dict:
    verify_program()
    if Path(sys.prefix) != ROOT / ".venv-paper":
        raise ValueError("Use this repository's .venv-paper/bin/python -B")
    if any(name.startswith("FREQTRADE__") for name in os.environ):
        raise ValueError("Freqtrade environment overrides are forbidden")
    if any("CZ-Agent" in str(value) for value in sys.path):
        raise ValueError("Legacy project import path is forbidden")
    manifest_path = ROOT / "deploy/upstream-source.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest["commit"] != ENGINE_COMMIT:
        raise ValueError("Wrong official engine commit")
    if snapshot is not None and sha(manifest_path) != snapshot["upstream_manifest_sha256"]:
        raise ValueError("Official source manifest changed")
    core = {k: v for k, v in manifest["files"].items() if k.startswith("freqtrade/")}
    for name, expected in core.items():
        checked({"path": name, "sha256": expected})
    actual_py = {str(p.relative_to(ROOT)) for p in (ROOT / "freqtrade").rglob("*.py")}
    extra = actual_py - {p for p in core if p.endswith(".py")}
    if extra != ALLOWED_UNUSED_EXTENSIONS:
        raise ValueError("Unexpected nonofficial engine namespace additions")
    origin = Path(importlib.util.find_spec("freqtrade").origin).resolve()
    if origin != ROOT / "freqtrade/__init__.py":
        raise ValueError("Freqtrade does not resolve to this repository")
    old = read_json("research/crypto/strategy_comparison_20261105/environment.json")
    dependencies = {name: importlib.metadata.version(name) for name in old["dependencies"]}
    if dependencies != old["dependencies"]:
        raise ValueError("A pinned legacy numerical dependency differs")
    if snapshot is not None:
        for name, expected in snapshot["dependencies"].items():
            if importlib.metadata.version(name) != expected:
                raise ValueError(f"Research dependency changed: {name}")
        if sys.version != snapshot["python"]:
            raise ValueError("Research Python version changed")
    return {
        "engine_commit": ENGINE_COMMIT,
        "official_package_files_verified": len(core),
        "unused_nonofficial_python_files": sorted(extra),
        "legacy_numeric_dependencies_equal": True,
        "freqtrade_origin": str(origin.relative_to(ROOT)),
    }


def verify_dataset(dataset: dict, payload: bool = True) -> dict:
    checked(dataset["parent_plan"])
    receipt_path = checked(dataset["data_receipt"])
    receipt = json.loads(receipt_path.read_text())
    directory = local_path(dataset["datadir"])
    if len(receipt) != dataset["file_count"]:
        raise ValueError("Dataset file count changed")
    total = 0
    for name, expected in receipt.items():
        path = local_path(str(directory / name))
        if not path.is_relative_to(directory) or not path.is_file():
            raise ValueError("Dataset entry missing or outside datadir")
        total += path.stat().st_size
        if total > 2_000_000_000:
            raise ValueError("Bounded dataset audit exceeded 2 GB")
        if payload and sha(path) != expected:
            raise ValueError(f"Dataset payload hash changed: {name}")
    if total != dataset["total_bytes"]:
        raise ValueError("Dataset byte count changed")
    for record in dataset["support_records"].values():
        checked(record)
    return {"dataset_id": dataset["id"], "files": len(receipt), "bytes": total,
            "payload_hashes_verified": payload, "economic_validation": False}


class _NoLegacyImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "cz" or fullname.startswith("cz.") or fullname == "freqtrade.research" or fullname.startswith("freqtrade.research."):
            raise ImportError(f"Nonofficial research namespace forbidden: {fullname}")
        return None


def install_runtime_guards() -> None:
    """Defense in depth for the calling offline worker; not a sandbox claim."""
    for name in sys.modules:
        if name == "cz" or name.startswith("cz.") or name == "freqtrade.research" or name.startswith("freqtrade.research."):
            raise RuntimeError("Legacy namespace already imported")
    sys.dont_write_bytecode = True
    sys.meta_path.insert(0, _NoLegacyImports())

    def no_network(*args, **kwargs):
        raise RuntimeError("Registered offline research attempted a network connection")

    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    socket.create_connection = no_network


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["spot_L", "spot_S", "futures_L", "futures_S", "all"])
    parser.add_argument("--metadata-only", action="store_true", help="Does not qualify payload readiness")
    args = parser.parse_args()
    manifest = json.loads((HERE / "source_environment.json").read_text())
    result = {"environment": verify_environment(manifest), "datasets": []}
    if args.dataset:
        datasets = json.loads((HERE / "data_manifest.json").read_text())["datasets"]
        for dataset in datasets:
            if args.dataset in ("all", dataset["id"]):
                result["datasets"].append(verify_dataset(dataset, not args.metadata_only))
    result.update(orders_enabled=False, writes_performed=False, financial_runs=0)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
