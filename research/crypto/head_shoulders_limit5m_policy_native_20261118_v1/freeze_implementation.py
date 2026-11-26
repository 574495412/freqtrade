"""Freeze implementation and official owners before any object probe."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent
OWNER = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")
sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
policy = json.loads((HERE / "policy_freeze.json").read_text())
for path, digest in policy["files"].items():
    if sha(path) != digest:
        raise ValueError("Preregistered policy input changed: " + path)
files = {**policy["files"], str(HERE / "policy_freeze.json"): sha(HERE / "policy_freeze.json")}
for path in HERE.glob("*.py"):
    files[str(path)] = sha(path)
manifest = json.loads((OWNER.parent / "FREQTRADE_SOURCE.json").read_text())
for relative, digest in manifest["files"].items():
    path = OWNER / relative
    if sha(path) != digest:
        raise ValueError("Official owner source differs: " + relative)
    files[str(path)] = digest
with (HERE / "implementation_freeze.json").open("x") as stream:
    json.dump(
        {
            "frozen_at": datetime.now(UTC).isoformat(),
            "before_object_probe": True,
            "financial_execution_admitted": False,
            "files": files,
        },
        stream,
        indent=2,
    )
    stream.write("\n")
print(json.dumps({"frozen_files": len(files), "sha256": sha(HERE / "implementation_freeze.json")}))
