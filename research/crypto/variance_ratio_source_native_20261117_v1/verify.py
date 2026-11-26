"""Independent vectorized predicate audit; does not import the source or diagnostic."""

# ruff: noqa: S101
# This is a verification entry point; assertions cannot be disabled.
from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
if not __debug__:
    raise RuntimeError("Verification requires assertions")


def main():
    result = json.loads((ROOT / "diagnostics.json").read_text())
    frames = []
    for receipt in result["data_receipts"]:
        path = Path(receipt["path"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == receipt["sha256"]
        frames.append(pd.read_feather(path))
    sol, btc = frames
    returns = np.log(sol.close / sol.close.shift(1))
    ratio = returns.rolling(5).sum().rolling(44).var(ddof=0) / (5 * returns.rolling(48).var(ddof=0))
    high, low = sol.close >= sol.high.rolling(48).max(), sol.close <= sol.low.rolling(48).min()
    vr_pass = ratio >= 1.15
    bull = btc.close.rolling(16).mean() > btc.close.rolling(48).mean()
    mask = sol.date.between(
        pd.Timestamp(result["window"]["start"]), pd.Timestamp(result["window"]["end"])
    )
    recomputed = {
        "bars": int(mask.sum()),
        "close_at_48_high": int((mask & high).sum()),
        "close_at_48_low": int((mask & low).sum()),
        "variance_ratio_pass": int((mask & vr_pass).sum()),
        "high_and_vr": int((mask & high & vr_pass).sum()),
        "low_and_vr": int((mask & low & vr_pass).sum()),
        "flat_long_order_probes": int((mask & high & vr_pass & bull).sum()),
        "flat_short_order_probes": int((mask & low & vr_pass & ~bull).sum()),
        "opposite_channel_long_exit": int((mask & (sol.close < sol.low.rolling(24).min())).sum()),
        "opposite_channel_short_exit": int((mask & (sol.close > sol.high.rolling(24).max())).sum()),
    }
    assert recomputed == result["counts"], (recomputed, result["counts"])
    for rel, digest in result["inputs"].items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest
    dist = json.loads((ROOT / "sdk_distribution_receipts.json").read_text())
    extra = json.loads((ROOT / "sdk_execution_receipts.json").read_text())
    with tarfile.open(dist["path"]) as tf:
        for row in extra:
            path = Path(row["path"])
            if not path.is_absolute():
                path = ROOT.parents[2] / path
            assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
            member = "flint_trading-1.1.0/" + path.relative_to(ROOT / "sdk_selected").as_posix()
            assert tf.extractfile(member).read() == path.read_bytes()
    verification = {
        "passed": True,
        "independent_counts": recomputed,
        "method": "pandas rolling windows; no candidate/source/diagnose.py imports",
        "sdk_archive_extra_modules_match": len(extra),
        "native_financial_wallets": 0,
        "account_profit_verified": False,
        "diagnostics_sha256": hashlib.sha256((ROOT / "diagnostics.json").read_bytes()).hexdigest(),
        "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (ROOT / "verification.json").write_text(json.dumps(verification, indent=2) + "\n")
    print(json.dumps(verification))


if __name__ == "__main__":
    main()
