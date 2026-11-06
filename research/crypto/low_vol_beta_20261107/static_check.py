"""Compare dynamic beta weights to each window's fixed starting split."""

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.low_vol_beta_20261107 import study
from research.crypto.low_vol_portfolio_20261107 import account, attribution
from research.crypto.low_vol_portfolio_20261107 import study as prior
from research.crypto.nfi_strength_allocation_20261107.audit import read
from research.crypto.nfi_strength_allocation_20261107.register import record
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked, save, sha


HERE = Path(__file__).resolve().parent
POLICY = HERE / "static_check_registration.json"
RESULT = HERE / "static_check_result.json"


def main():
    result = read(study.RESULT)
    if POLICY.exists() or result["registration_sha256"] != sha(study.POLICY):
        raise ValueError("Static control already registered or parent differs")
    save(
        POLICY,
        {
            "registered_at": datetime.now(UTC).isoformat(),
            "sources": [record(Path(__file__)), record(study.RESULT), record(study.POLICY)],
            "rule": (
                "Diagnostic after primary outcomes: at 1.6x gross, fix the long share to "
                "the BetaReserve candidate's first known decision in each independent "
                "window. Allow low-volatility ranks and prices to evolve as before. "
                "Same cash, fees, constraints, and risk. Keep prior failures intact."
            ),
            "orders_enabled": False,
        },
    )
    evidence = read(checked(result["signals"]))
    parent = read(prior.RESULT)
    contract = read(prior.POLICY)
    plan = read(checked(contract["parent"]))
    symbols = contract["symbols"]
    raw = {s: prior.candles(plan, s) for s in symbols}
    ranks = prior.weights(raw)
    funding = {
        s: attribution.funding_from_receipt(row, s)
        for s, row in read(checked(parent["funding_receipts"])).items()
    }
    rules = account.snapshot(
        read(checked(plan["market_snapshot"])), [prior.symbol_pair(s) for s in symbols]
    )
    output = {}
    for name, (start, end) in prior.WINDOWS.items():
        grid = pd.date_range(prior.clock(start), prior.clock(end), freq="5min")
        share = evidence[grid[0].isoformat()]["long_fraction"]
        fixed = {
            t: np.where(w > 0, share / 5, np.where(w < 0, -(1 - share) / 5, 0))
            for t, w in ranks.items()
        }
        root = prior.leverage.a13.OUT / "5m" if name == "2024" else prior.long_mark.OUT
        admission = read(root / "admission.json")["marks"]
        data = account.make_inputs(grid, raw, funding, admission, prior.symbol_pair)
        output[name] = {"fixed_long_share": share, "accounts": {}}
        for cost in prior.COSTS:
            key = f"{name}_StaticReserve_{cost}bps"
            row = account.simulate(grid, data, fixed, rules, 1.6, cost, study.OUT / key)
            output[name]["accounts"][str(cost)] = row
            print(key, row["net"], row["drawdown"], flush=True)
    save(RESULT, {"registration_sha256": sha(POLICY), "windows": output})


if __name__ == "__main__":
    main()
