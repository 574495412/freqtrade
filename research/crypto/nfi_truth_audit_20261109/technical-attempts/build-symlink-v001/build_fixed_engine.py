"""Reproduce the local diagnostic patch without changing the pinned live engine."""

import argparse
import copy
import json
import shutil
import subprocess
from pathlib import Path

from research.crypto.nfi_blacklist_rules_20261109.inputs import read, save, sha


HERE = Path(__file__).resolve().parent
BASE = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")
MANIFEST = BASE.parent / "FREQTRADE_SOURCE.json"
MODULE = "freqtrade/optimize/analysis/lookahead.py"


def build(output):
    registered = read(HERE / "cache-fix-registration.json")
    manifest = read(MANIFEST)
    if sha(MANIFEST) != "c0d66d07ecd46b6506af8f5408c61185adb8fc0f63d1e7652f5bf4a104a3bc64":
        raise ValueError("Pinned upstream manifest changed")
    patch = HERE / "native-cache-isolation.patch"
    if sha(patch) != registered["patch_sha256"]:
        raise ValueError("Diagnostic patch changed")
    output.mkdir(parents=True, exist_ok=False)
    engine = output / "engine"
    engine.mkdir()
    for name, digest in manifest["files"].items():
        original, target = BASE / name, engine / name
        if sha(original) != digest:
            raise ValueError("Upstream source changed: " + name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
    for name, target in manifest.get("symlinks", {}).items():
        path = engine / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=engine, check=True)
    subprocess.run(["git", "apply", str(patch)], cwd=engine, check=True)
    changed = [name for name, digest in manifest["files"].items() if sha(engine / name) != digest]
    if changed != [MODULE]:
        raise ValueError("Unexpected native patch scope")
    result = copy.deepcopy(manifest)
    result["files"][MODULE] = sha(engine / MODULE)
    result["local_extension"] = {
        "name": "lookahead per-replay runtime state isolation",
        "modified_upstream_files": [MODULE],
        "upstream_manifest_sha256": sha(MANIFEST),
        "not_an_upstream_fix_claim": True,
    }
    save(output / "FREQTRADE_SOURCE.json", result)
    if sha(output / "FREQTRADE_SOURCE.json") != registered["engine_manifest_sha256"]:
        raise ValueError("Rebuilt native diagnostic engine differs from the tested engine")
    print(
        json.dumps(
            {
                "reproduced": True,
                "files": len(result["files"]),
                "changed_modules": changed,
                "live_engine_modified": False,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output.resolve())
