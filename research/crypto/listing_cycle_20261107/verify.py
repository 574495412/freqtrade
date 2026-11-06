"""Verify account receipts, reproduce controls, and describe fixed-label selection."""

from collections import defaultdict
from pathlib import Path
from statistics import mean, median

from freqtrade.research.data import read, save, sha
from research.crypto.listing_cycle_20261107 import study
from research.crypto.nfi_strength_allocation_20261107.register import record
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked


def selection(contract, market, signals):
    start = study.replay.bar_number(contract["common_start"] + "T00:00:00Z")
    labels = defaultdict(list)
    for name, state in signals.items():
        item = market[name]
        if item.launch < start:
            continue
        group = (
            "unknown" if state["score"] is None else "admitted" if state["accepted"] else "rejected"
        )
        labels[group].append(study.observation(item, contract))
    return {
        key: {
            "symbols": len(values),
            "mean_40bps_fixed_short": mean(values),
            "median_40bps_fixed_short": median(values),
            "positive": sum(x > 0 for x in values),
        }
        for key, values in labels.items()
    }


def main():
    report = read(study.RESULT)
    p = read(study.POLICY)
    if sha(study.POLICY) != report["registration_sha256"]:
        raise ValueError("Result registration changed")
    for ref in p["sources"]:
        checked(ref)
    original2024 = read(study.a06.HERE / "result.json")["accounts"]
    original = read(study.a06.HERE / "regime_result.json")["cohorts"]
    inputs = study.window_inputs()
    output = {}
    for window, info in report["windows"].items():
        contract, _, market, _, _ = inputs[window]
        signals = read(checked(info["signal_receipt"]))["signals"]
        evidence = {"fixed_labels_by_signal": selection(contract, market, signals), "accounts": {}}
        for key, account in info["accounts"].items():
            model = account["economics"]
            if key.startswith("AllLaunches"):
                base = (
                    original2024[f"{window}/{key}"]
                    if window == "2024"
                    else original[window]["accounts"][key]
                )
                if model != base["economics"]:
                    raise ValueError(
                        f"Full account failed exact baseline reproduction: {window} {key}"
                    )
            folder = study.OUT / window / key
            hashes = account["verification"]["hashes"]
            for name, digest in hashes.items():
                checked({"path": str(folder / name), "sha256": digest})
            if key.startswith("MatureCycle"):
                for event in read(folder / "events.json"):
                    if event["kind"] != "entry":
                        continue
                    signal = signals[event["symbol"]]
                    if not signal["accepted"] or event["clock"] != signal["decision"] + 1:
                        raise ValueError("Candidate entry without causal permission")
            evidence["accounts"][key] = {
                "curve": record(folder / "nav.parquet"),
                "closes": model["completed"],
                "five_minute_nav_points": account["verification"]["nav_points"],
            }
        output[window] = evidence
    save(
        Path(__file__).with_name("verification.json"),
        {
            "result_sha256": sha(study.RESULT),
            "source_sha256": sha(Path(__file__)),
            "baseline_exact": True,
            "windows": output,
        },
    )


if __name__ == "__main__":
    main()
