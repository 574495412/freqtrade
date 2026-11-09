"""Freeze the storage-only resource repair with unchanged economic contracts."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

from research.crypto.nfi80_historical_universe_20261109.prepare import HERE as BASE
from research.crypto.nfi80_historical_universe_20261109.prepare import OUT, read, ref, save


HERE = Path(__file__).resolve().parent


def main():
    if (HERE / "plans.json").exists():
        raise FileExistsError("Preserve registered storage revision")
    prior = BASE / "v002"
    for name in ["input-protocol.json", "schedule-summary.json", "preflight-result.json"]:
        shutil.copy2(prior / name, HERE / name)
    protocol = (prior / "PROTOCOL.md").read_text()
    protocol += "\nTechnical v003: v002 exhausted available disk while duplicating full indicator and temporary trimmed caches, before any account replay clock or economic result. Preserve its logs and generated-file inventory; remove only failed generated cache. Use the exact native trim lazily for date discovery, then release each already converted source frame after the native analyzed-provider copy and exact row blocks exist. Native export=signals is rejected because that mode needs the consumed temporary map afterward. No prices, precision, source rules, fills, fees or risk settings change. Enforce a 1.5 GiB free-disk guard and require a new full fixed80 exact NAV/trade parity before daily economic accounts.\n"
    (HERE / "PROTOCOL.md").write_text(protocol)
    runner = ref(HERE / "native_runner.py")
    plans = {}
    for case, path in [
        ("parity", BASE / "parity-technical-plan.json"),
        ("original", prior / "original-plan.json"),
        ("flatboost", prior / "flatboost-plan.json"),
    ]:
        plan = read(path)
        plan.update(
            engine_source=str(OUT / "engine_v002"),
            engine_manifest=ref(prior / "engine-manifest.json"),
            extension_patch=ref(prior / "native.patch"),
            row_storage=ref(HERE / "row_storage.py"),
            runner_sha256=runner["sha256"],
            historical_universe_protocol=ref(HERE / "PROTOCOL.md"),
            prior_technical_plan=ref(path),
            registered_at=datetime.now(UTC).isoformat(),
            storage_consumption_only_revision=True,
            dynamic_economic_results_observed=False,
        )
        save(HERE / (case + "-plan.json"), plan)
        plans[case] = ref(HERE / (case + "-plan.json"))
    old = read(prior / "plans.json")
    save(
        HERE / "plans.json",
        {
            "plans": plans,
            "sources": old["sources"],
            "new_sources": {p.name: ref(p) for p in HERE.glob("*.py")},
            "runner": runner,
            "protocol": ref(HERE / "PROTOCOL.md"),
            "old_parity": old["old_parity"],
            "costs": [15, 8],
            "prior_registration": ref(prior / "plans.json"),
            "orders_enabled": False,
            "resource_failure_inventory": ref(
                OUT / "accounts-v002/original_15bps/generated-cache-cleanup.json"
            ),
        },
    )
    print({"version": "v003", "economic_changes": False, "full_native_accounts": 5})


if __name__ == "__main__":
    main()
