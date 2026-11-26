"""Preserve the failed startup admission; bind supported past-only warmup."""

import json
from datetime import UTC, datetime
from pathlib import Path

from research.crypto.hawkes_source_native_20261117_v1.prepare_plan import record

HERE = Path(__file__).resolve().parent


def main():
    assert not (HERE / "plan_attempt2.json").exists()
    original = json.loads((HERE / "implementation_freeze.json").read_text())
    assert all(record(p)["sha256"] == sha for p, sha in original["files"].items())
    plan = json.loads((HERE / "plan.json").read_text())
    plan.update(registered_at=datetime.now(UTC).isoformat(),
                output_root=str(Path(plan["output_root"]) / "attempt2"))
    history = {pair: record(Path(plan["datadir"]) / "futures" /
               (pair.replace("/", "_").replace(":", "_") + "-1h-futures.feather"))
               for pair in plan["pairs"]}
    for candidate in plan["candidates"]:
        config = json.loads(Path(candidate["config"]["path"]).read_text())
        config.update(strategy="HawkesStrategyOffline", research_hawkes_history=history)
        path = HERE / (candidate["name"] + "_attempt2.json")
        path.write_text(json.dumps(config, indent=2) + "\n")
        candidate.update(strategy="HawkesStrategyOffline", config=record(path))
        candidate["source_files"]["HawkesStrategyOffline.py"] = record(HERE / "HawkesStrategyOffline.py")
    extra = [HERE / name for name in ["HawkesStrategyOffline.py", "test_offline_history.py",
             "offline_history_validation.json", "preflight_attempt2.py", "analyze_attempt2.py",
             "execute_attempt2.py", "register_attempt2.py", "technical_retry_attempt2.json",
             "implementation_freeze.json"]]
    plan["additional_source_seals"] += [record(p) for p in extra] + list(history.values())
    Path(plan["output_root"]).mkdir(exist_ok=False)
    (HERE / "plan_attempt2.json").write_text(json.dumps(plan, indent=2) + "\n")
    freeze = {str(p.resolve()): record(p)["sha256"] for p in [HERE / "plan_attempt2.json", *extra]}
    freeze.update(original["files"])
    freeze.update({c["config"]["path"]: c["config"]["sha256"] for c in plan["candidates"]})
    (HERE / "registration_attempt2_freeze.json").write_text(json.dumps({
        "frozen_at": datetime.now(UTC).isoformat(), "financial_returns_seen": False,
        "same_source_math_and_economic_contract": True, "files": freeze}, indent=2) + "\n")
    print("Registered8nativewalletsattempt2;0financialstarts")


if __name__ == "__main__":
    main()
