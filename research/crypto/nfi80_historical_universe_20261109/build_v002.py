# ruff: noqa: E501
"""Technical schema-registration repair before any dynamic account economics."""

import difflib
import shutil
from datetime import UTC, datetime

from research.crypto.nfi80_historical_universe_20261109.prepare import (
    HERE,
    OUT,
    PLAN,
    read,
    ref,
    save,
    sha,
)


def main():
    target = HERE / "v002"
    engine = OUT / "engine_v002"
    if engine.exists() or (target / "plans.json").exists():
        raise FileExistsError("Preserve technical revision")
    target.mkdir(exist_ok=True)
    shutil.copytree(OUT / "engine", engine, ignore=shutil.ignore_patterns("__pycache__"))
    name = "freqtrade/constants.py"
    text = (engine / name).read_text()
    needle = "AVAILABLE_PAIRLISTS = [\n"
    if text.count(needle) != 1:
        raise ValueError("Native plugin registry does not uniquely match")
    (engine / name).write_text(text.replace(needle, needle + '    "HistoricalVolumePairList",\n'))
    manifest = read(HERE / "engine-manifest.json")
    manifest["files"][name] = sha(engine / name)
    manifest["modified_upstream_files"].append(name)
    patch = []
    upstream = __import__("pathlib").Path(PLAN["engine_source"])
    for relative in manifest["modified_upstream_files"]:
        patch.extend(
            difflib.unified_diff(
                (upstream / relative).read_text().splitlines(True),
                (engine / relative).read_text().splitlines(True),
                fromfile="a/" + relative,
                tofile="b/" + relative,
            )
        )
    (target / "native.patch").write_text("".join(patch))
    manifest.update(
        patch=ref(target / "native.patch"),
        prior_extension_manifest=ref(HERE / "engine-manifest.json"),
        technical_revision="Register historical plugin name in native AVAILABLE_PAIRLISTS for schema validation; no financial rule change",
    )
    save(target / "engine-manifest.json", manifest)
    protocol = (HERE / "PROTOCOL.md").read_text()
    protocol += "\nTechnical v002: the first dynamic invocation failed configuration schema before Backtesting. Add only HistoricalVolumePairList to native AVAILABLE_PAIRLISTS in constants.py, with a new isolated engine, manifest and output accounts. The fixed80 parity result still proves the unchanged row storage and financial engine path; constants change is a plugin-name registration only. Preserve the failed run and all v001 plans. No new dynamic returns or hypothesis changes preceded this repair.\n"
    (target / "PROTOCOL.md").write_text(protocol)
    for file in ["input-protocol.json", "schedule-summary.json", "parity-result.json"]:
        shutil.copy2(HERE / file, target / file)
    plans = {}
    for case in ["original", "flatboost"]:
        prior = HERE / (case + "-plan.json")
        plan = read(prior)
        plan.update(
            engine_source=str(engine),
            engine_manifest=ref(target / "engine-manifest.json"),
            extension_patch=ref(target / "native.patch"),
            prior_technical_plan=ref(prior),
            historical_universe_protocol=ref(target / "PROTOCOL.md"),
            selection_protocol=ref(target / "PROTOCOL.md"),
            registered_at=datetime.now(UTC).isoformat(),
            technical_revision_reason="Native pairlist schema registry required; first dynamic invocation failed before account replay; economics unchanged",
            dynamic_economic_results_observed=False,
        )
        save(target / (case + "-plan.json"), plan)
        plans[case] = ref(target / (case + "-plan.json"))
    old = read(HERE / "plans.json")
    save(
        target / "plans.json",
        {
            "plans": plans,
            "sources": old["sources"],
            "runner": old["runner"],
            "protocol": ref(target / "PROTOCOL.md"),
            "costs": [15, 8],
            "prior_registration": ref(HERE / "plans.json"),
            "old_parity": ref(HERE / "parity-result.json"),
            "new_sources": {p.name: ref(p) for p in target.glob("*.py")},
            "failed_invocation": ref(OUT / "original_15bps/failure.json"),
            "orders_enabled": False,
        },
    )
    print({"revision": "v002", "changed_financial_rules": False, "accounts": 4})


if __name__ == "__main__":
    main()
