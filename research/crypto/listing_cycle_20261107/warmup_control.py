"""Isolate the already-registered history warmup from the positive-score requirement."""

from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.listing_cycle_20261107 import study
from research.crypto.nfi_strength_allocation_20261107.register import record
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked


def main():
    parent = read(study.RESULT)
    if parent["registration_sha256"] != sha(study.POLICY):
        raise ValueError("Original study changed")
    base = study.OUT / "warmup_control"
    base.mkdir()
    policy = study.HERE / "warmup_control_registration.json"
    if policy.exists():
        raise FileExistsError("Warmup diagnostic already registered")
    save(
        policy,
        {
            "sources": [record(Path(__file__)), record(study.POLICY), record(study.RESULT)],
            "rule": (
                "Using the exact precomputed release receipts, trade whenever the original "
                "candidate has twelve known past cohorts, regardless of score sign. "
                "Keep account, window, fees and risk unchanged. Original outcomes known. "
                "This diagnostic will not replace or promote the preregistered candidate."
            ),
            "orders_enabled": False,
        },
    )
    output = {}
    for window, (contract, panel, market, _, _) in study.window_inputs().items():
        signals = read(checked(parent["windows"][window]["signal_receipt"]))["signals"]
        first = study.replay.bar_number(pd.Timestamp(contract["common_start"], tz="UTC"))
        selected = {
            name: m
            for name, m in market.items()
            if m.launch >= first and name in signals and signals[name]["score"] is not None
        }
        folder = base / window
        folder.mkdir()
        study.audit.OUT = folder
        accounts = {}
        for cost in contract["cost_bps"]:
            accounts[str(cost)] = study.run_account(
                folder, contract, panel, selected, "WarmupOnly", cost
            )
            for row in read(folder / f"WarmupOnly_{cost}bps" / "events.json"):
                if row["kind"] == "entry":
                    info = signals[row["symbol"]]
                    if info["score"] is None or row["clock"] != info["decision"] + 1:
                        raise ValueError("Warmup-only entry lacks an authorized decision")
        output[window] = {"selected": len(selected), "accounts": accounts}
    save(
        study.HERE / "warmup_control_result.json",
        {"registration_sha256": sha(policy), "windows": output},
    )


if __name__ == "__main__":
    main()
