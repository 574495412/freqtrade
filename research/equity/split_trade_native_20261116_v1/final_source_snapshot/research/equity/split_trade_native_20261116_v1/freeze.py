"""Bind actual native extension and inherited fixture owners before starts."""

import json
import sys

from bootstrap import HERE, ROOT, boot, save, sha


def main():
    target = HERE / "implementation_freeze.json"
    if target.exists():
        raise RuntimeError("Do not replace frozen native implementation")
    manifest = boot()
    import test_sessions  # noqa: F401 - load the inherited fixture dependencies to bind their bytes

    paths = set(HERE.glob("*.py"))
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if not path:
            continue
        from pathlib import Path

        source = Path(path).absolute()
        if (
            source.is_file()
            and source.suffix == ".py"
            and (
                source.is_relative_to(ROOT / "freqtrade")
                or source.is_relative_to(ROOT / "research/equity")
            )
        ):
            paths.add(source)
    paths.add(HERE.with_name("start_lifecycle_native_20261115_v1") / "audit_trace.py")
    paths.update(
        HERE / name
        for name in [
            "protocol.json",
            "fixture_definitions.json",
            "source_receipts.json",
            "core_seam_audit.json",
        ]
    )
    paths.update((HERE / "sources").iterdir())
    save(target, {str(path): sha(path) for path in sorted(paths)})
    save(
        HERE / "native_owner.json",
        {
            "upstream_commit": manifest["commit"],
            "verified_upstream_files": len(manifest["files"]),
            "actual_core_override": str(ROOT / "freqtrade/optimize/backtesting.py"),
            "local_extension_not_official_equity_support": True,
            "frozen_paths": len(paths),
            "historical_accounts": 0,
        },
    )
    print("Frozen", len(paths), "paths;595upstream plus explicit local core seam")


if __name__ == "__main__":
    main()
