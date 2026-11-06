"""Recompute signal pulses from raw clock positions and reconcile every NAV point."""

from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.breadth_diffusion_20261107 import verify as account_audit


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = ROOT / "user_data/crypto_quant"
OUT = DATA / "basis_flush_20261107_v001"
SPOT = DATA / "spot_baseline_data_long_20261105_v001/candles"
PERP = DATA / "breadth_diffusion_20261107_v001/data"


def confirm_pulses(plan, pulses, symbol, bars, spot, features):
    spec = plan["signal"]
    warmup = pd.Timestamp(plan["warmup_start"], tz="UTC")
    closes = bars.loc[warmup:].close
    spot = spot.loc[closes.index]
    relative = np.log(closes.to_numpy() / spot.to_numpy())
    close = closes.to_numpy()
    hours = features.loc[warmup:].index
    eligible = features.eligible
    cutoff = pd.Timedelta(minutes=5 + spec["information_buffer_minutes"])
    last = closes.index.get_indexer(hours - cutoff)
    expected = {case: np.zeros(len(hours), dtype=np.int8) for case in plan["cases"]}
    prior_ready = dict.fromkeys(expected, False)
    resume = dict.fromkeys(expected, 0)
    for i, (hour, end) in enumerate(zip(hours, last, strict=True)):
        gate_time = hour if hour.hour else hour - pd.Timedelta(hours=1)
        live = end >= spec["scan_bars"] + spec["flush_bars"] - 1 and eligible.loc[gate_time]
        cases = dict.fromkeys(expected, False)
        if live:
            for j in range(end - spec["scan_bars"] + 1, end + 1):
                change = close[j] / close[j - spec["flush_bars"]] - 1
                bounce = close[end] / close[j] - 1
                if change <= spec["flush_return_at_most"] and bounce >= spec["rebound_minimum"]:
                    cases["PriceFlush"] = True
                    upper = j - spec["basis_reference_lag_bars"] + 1
                    lower = upper - spec["basis_reference_bars"]
                    if lower >= 0:
                        delta = relative[j] - np.median(relative[lower:upper])
                        cases["BasisFlush"] |= delta <= spec["relative_log_basis_at_most"]
        for case, ready in cases.items():
            if ready and not prior_ready[case] and i >= resume[case]:
                resume[case] = i + spec["pulse_hours"]
                expected[case][i : resume[case]] = 1
            prior_ready[case] = ready
    start = pd.Timestamp(plan["start"], tz="UTC")
    for case, signal in expected.items():
        recorded = pulses[case][symbol]
        if not recorded.index.equals(hours):
            raise ValueError(f"Signal calendar mismatch for {symbol}")
        active = hours >= start
        np.testing.assert_array_equal(
            signal[active], recorded.to_numpy()[active], err_msg=f"{case} {symbol}"
        )


def main():
    registration = HERE / "registration.json"
    plan = read(registration)
    for name, digest in plan["checksums"].items():
        if sha(ROOT / name) != digest:
            raise ValueError(f"Frozen input mismatch: {name}")
    result = HERE / "result.json"
    receipt = read(result)
    if receipt["registration_sha256"] != sha(registration):
        raise ValueError("Unregistered results")
    signal_files = {case: OUT / f"{case}-signals.parquet" for case in plan["cases"]}
    pulses = {case: pd.read_parquet(path) for case, path in signal_files.items()}
    panel = {}
    for s in plan["symbols"]:
        bars = pd.read_parquet(PERP / f"{s}_5m.parquet")
        funding = pd.read_parquet(PERP / f"{s}_funding.parquet")
        features = pd.read_parquet(PERP / f"{s}_features.parquet")
        spot = (
            pd.read_feather(SPOT / f"{s.removesuffix('USDT')}_USDT-5m.feather")
            .set_index("date")
            .close
        )
        confirm_pulses(plan, pulses, s, bars, spot, features)
        panel[s] = (bars, funding)
    account_audit.OUT = OUT
    audited = {}
    for name in receipt["accounts"]:
        case = name.rsplit("_", 1)[0]
        for order in read(OUT / name / "decisions.json"):
            clock, symbol = pd.Timestamp(order["time"]), order["symbol"]
            if order["side"] != 1 or pulses[case].at[clock, symbol] != 1:
                raise ValueError(f"Decision lacked registered long signal: {order}")
        audited[name] = account_audit.audit_account(plan, panel, name)
    save(
        HERE / "verification.json",
        {
            "registration_sha256": sha(registration),
            "result_sha256": sha(result),
            "signal_sha256": {case: sha(path) for case, path in signal_files.items()},
            "signal_checks": (
                "All in-sample hourly pulses independently reconstructed from explicitly "
                "indexed causal raw windows. Every entry decision required an active pulse."
            ),
            "modeled_ledger_passed": True,
            "accounts": audited,
        },
    )


if __name__ == "__main__":
    main()
