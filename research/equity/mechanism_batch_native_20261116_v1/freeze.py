"""Freeze exact implementation and owning APIs before real diagnostics."""

import json

from bootstrap import HERE, save, sha


def main():
    target = HERE / "implementation_freeze.json"
    if target.exists():
        raise ValueError("Never replace the initial frozen implementation")
    paths = [p for p in HERE.glob("*.py")]
    paths += [
        HERE / name
        for name in [
            "protocol.json",
            "ownership.json",
            "input_reference.json",
            "source_receipts.json",
            "source_intake.json",
            "source_witnesses.json",
        ]
    ]
    hashes = {str(p): sha(p) for p in paths}
    for path, digest in json.loads((HERE / "ownership.json").read_text())["files"].items():
        if sha(path) != digest:
            raise ValueError(f"Owning API changed before freeze: {path}")
        hashes[path] = digest
    for rec in json.loads((HERE / "source_receipts.json").read_text())["records"]:
        if "path" in rec:
            hashes[rec["path"]] = sha(rec["path"])
    save(target, hashes)
    print("Frozen paths", len(hashes), flush=True)


if __name__ == "__main__":
    main()
