"""Verify funding visibility and pulses, then reconstruct the modeled cash ledger."""

from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.breadth_diffusion_20261107 import verify as ledger


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/funding_breakout_20261107_v001"


def expected_pulses(plan, bars, features, funding):
    spec = plan["signal"]
    hours = features.index
    closes = bars.close.reindex(hours - pd.Timedelta(minutes=10)).to_numpy()
    eligible = features.eligible.to_numpy()
    seen = hours - pd.Timedelta(minutes=spec["funding_visibility_minutes"])
    recent = funding.index.searchsorted(seen, side="right") - 1
    stale = (
        funding.index.searchsorted(
            seen - pd.Timedelta(days=spec["stale_funding_days"]), side="right"
        )
        - 1
    )
    funding_values = funding.funding_rate.to_numpy()
    length = len(hours)
    output = {case: np.zeros(length, dtype=np.int8) for case in plan["cases"]}
    expiry = dict.fromkeys(output, 0)
    floors = dict.fromkeys(output, 0.0)
    previous = dict.fromkeys(output, False)
    window = spec["lookback_hours"]
    for i in range(window + 1, length):
        close = closes[i]
        level = np.max(closes[i - window : i])
        liquid = eligible[i - 1] if hours[i].hour == 0 else eligible[i]
        price = liquid and close >= level * (1 + spec["breakout_buffer"])
        ready = dict.fromkeys(output, price)
        for case, j in (("RecentFunding", recent[i]), ("StaleFunding", stale[i])):
            if j < 2 or not price:
                ready[case] = False
            else:
                first, second, third = funding_values[j - 2 : j + 1]
                ready[case] = (
                    second < 0 and third < 0 and first + second + third <= spec["min_funding_sum"]
                )
        for case, signal in ready.items():
            if close < floors[case]:
                expiry[case] = 0
            if signal and not previous[case] and i >= expiry[case]:
                expiry[case] = i + spec["pulse_hours"]
                floors[case] = level * (1 - spec["invalidation_below_level"])
            output[case][i] = int(i < expiry[case])
            previous[case] = signal
    return output


def main():
    registration = HERE / "registration.json"
    result = HERE / "result.json"
    plan = read(registration)
    summary = read(result)
    data = OUT / "data"
    receipt_path = data / "receipts.json"
    for name, digest in plan["checksums"].items():
        if sha(ROOT / name) != digest:
            raise ValueError(f"Registered code mismatch: {name}")
    if summary["registration_sha256"] != sha(registration) or summary[
        "input_receipts_sha256"
    ] != sha(receipt_path):
        raise ValueError("Result lineage mismatch")
    for name, digest in read(receipt_path).items():
        if sha(data / name) != digest:
            raise ValueError(f"Prepared data mismatch: {name}")
    signals = {case: OUT / f"{case}-signals.parquet" for case in plan["cases"]}
    recorded = {case: pd.read_parquet(path) for case, path in signals.items()}
    panel = {}
    checks = 0
    for s in plan["symbols"]:
        bars = pd.read_parquet(data / f"{s}_5m.parquet")
        features = pd.read_parquet(data / f"{s}_features.parquet")
        funding = pd.read_parquet(data / f"{s}_funding.parquet")
        reconstructed = expected_pulses(plan, bars, features, funding)
        active = features.index >= pd.Timestamp(plan["start"], tz="UTC")
        for case, expected in reconstructed.items():
            if not recorded[case].index.equals(features.index):
                raise ValueError("Signal calendar mismatch")
            np.testing.assert_array_equal(
                expected[active], recorded[case][s].to_numpy()[active], err_msg=f"{s} {case}"
            )
            checks += int(active.sum())
        panel[s] = (bars, funding)
    ledger.OUT = OUT
    verified = {}
    for name in summary["accounts"]:
        case = name.rsplit("_", 1)[0]
        for order in read(OUT / name / "decisions.json"):
            t = pd.Timestamp(order["time"])
            if order["side"] != 1 or recorded[case].at[t, order["symbol"]] != 1:
                raise ValueError(f"Unexplained long decision: {order}")
        verified[name] = ledger.audit_account(plan, panel, name)
        print("verified", name, flush=True)
    save(
        HERE / "verification.json",
        {
            "registration_sha256": sha(registration),
            "result_sha256": sha(result),
            "verified_signal_symbol_hours": checks,
            "signal_receipts": {case: sha(path) for case, path in signals.items()},
            "accounts": verified,
            "paper_eligible": False,
        },
    )


if __name__ == "__main__":
    main()
