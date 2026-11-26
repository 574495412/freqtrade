"""Bind the actual orders to the isolated factor scores, then reconcile cash."""

from pathlib import Path

import pandas as pd

from research.crypto.turtle55_native_20261125_v1 import audit as reconcile

from .run_financial import CASE, HERE, NAME


def check_trades(scores):
    directions = {}
    for date, row in scores.iterrows():
        ordered = sorted(row.items(), key=lambda item: item[1])
        directions[date] = ({name for name, _ in ordered[:3]}, {name for name, _ in ordered[-3:]})

    def verify(trade, daily, opens):
        opened = pd.Timestamp(trade["open_timestamp"], unit="ms", tz="UTC")
        assert opened.weekday() == 0 and opened == opened.normalize() + pd.Timedelta(hours=1)
        expected = directions[opened.normalize()][not trade["is_short"]]
        assert trade["pair"] in expected
        initial, final = trade["orders"]
        assert initial["ft_is_entry"] and not final["ft_is_entry"]
        assert initial["order_filled_timestamp"] == trade["open_timestamp"]
        assert final["order_filled_timestamp"] == trade["close_timestamp"]
        if trade["exit_reason"] == "week_end":
            closed = pd.Timestamp(trade["close_timestamp"], unit="ms", tz="UTC")
            assert closed == opened.normalize() + pd.Timedelta(days=7)
        else:
            assert trade["exit_reason"] in {"force_exit", "stop_loss", "liquidation"}
        return 0

    return verify


def main():
    root = HERE.parents[2] / "user_data/crypto_quant" / HERE.name
    factor = pd.read_csv(root / "week_high/full/factor.csv", index_col=0, parse_dates=True)
    audit = reconcile.read(HERE / "financial_freeze.json")
    assert reconcile.sha(root / "week_high/full/factor.csv") == audit["factor_csv_sha256"]
    # The last candle window has one additional, still-open weekly factor slate.
    # Its scores were frozen with the other native decisions before any fills.
    final = pd.read_csv(root / "final_week_factor.csv", index_col=0, parse_dates=True)
    assert reconcile.sha(root / "final_week_factor.csv") == audit["last_factor_csv_sha256"]
    factor = pd.concat([factor, final], verify_integrity=True)
    reconcile.NAME, reconcile.HERE = NAME, HERE
    reconcile.audit_signal = check_trades(factor)
    plan = reconcile.read(reconcile.PARENT / "plan.json")
    grid = pd.date_range(plan["start"], plan["comparison_end"], freq="h").as_unit("ns")
    account, candidate = reconcile.account(CASE, plan, grid)
    reconcile.save(HERE / f"{CASE}.audit.json", account)
    baseline = next(row for row in plan["benchmark_nfi1000"] if row["cost_bps"] == 15)
    _, original = reconcile.curve(reconcile.checked(baseline["hourly_curve"]), grid)
    control = plan["nfi500_dependency"]
    nfi_name = "nfi40_x8_500_cost15"
    admitted = reconcile.read(HERE.parent / "head_shoulders_audit_evidence_v3_20261118_v1"
                              / "audited_complete_accounts/admission.json")["nfi_accounts"]["15"]
    assert admitted["passed"] and admitted["candidate"] == nfi_name
    path = Path(control["output_root"]) / nfi_name / "hourly_equity.parquet"
    _, nfi = reconcile.curve(reconcile.checked({"path": str(path), "sha256": admitted["observed_nav_sha256"]}), grid)
    _, result = reconcile.fixed_combo(candidate, nfi, original, plan["periods"], account["status"] == "VERIFIED")
    reconcile.save(HERE / "combination_cost15.json", result)


if __name__ == "__main__":
    main()
