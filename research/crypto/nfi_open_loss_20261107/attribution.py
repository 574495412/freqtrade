"""Compare exit-triggered campaigns to original 2x campaigns with the same entry."""

from pathlib import Path

from research.crypto.nfi_open_loss_20261107 import study
from research.crypto.nfi_strength_allocation_20261107.audit import native_trades, read
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked, save, sha


def main():
    parent_plan = read(study.leverage.check()["2024_Native2x"])
    parent = read(study.leverage.RESULT)["stages"]["2024"]["accounts"]
    policy = read(study.POLICY)
    report = read(study.RESULT)
    if report["registration_sha256"] != sha(study.POLICY):
        raise ValueError("Account results differ from registration")
    plan = read(checked(policy["plan"]))
    accounts = {}
    for cost in study.COSTS:
        key = f"2024_Native2x_{cost}bps"
        base_receipt = parent[key]
        baseline, base_detail = native_trades(
            parent_plan,
            checked(base_receipt["hourly_equity"]).parent,
            "NostalgiaForInfinityX8",
            cost,
        )
        candidate_name = f"2024_{study.NAME}_{cost}bps"
        new_receipt = report["accounts"][candidate_name]
        overlay, new_detail = native_trades(plan, study.OUT / candidate_name, study.NAME, cost)
        if any(
            reference["archive_sha256"] != actual["archive_sha256"]
            for reference, actual in ((base_receipt, base_detail), (new_receipt, new_detail))
        ):
            raise ValueError("Source archive changed")
        old = {study.date_key(t): t for t in baseline}
        rows = []
        for t in overlay:
            if t["exit_reason"] != study.REASON:
                continue
            reference = old.get(study.date_key(t))
            rows.append(
                {
                    "pair": t["pair"],
                    "short": t["is_short"],
                    "open_utc": t["open_date"],
                    "overlay_close_utc": t["close_date"],
                    "overlay_net_usdt": float(t["profit_abs"]),
                    "uniform_close_utc": reference["close_date"] if reference else None,
                    "uniform_net_usdt": float(reference["profit_abs"]) if reference else None,
                    "uniform_exit_reason": reference["exit_reason"] if reference else None,
                }
            )
        matched = [r for r in rows if r["uniform_net_usdt"] is not None]
        accounts[str(cost)] = {
            "rows": rows,
            "matched": len(matched),
            "unmatched": len(rows) - len(matched),
            "matched_original_net_usdt": sum(r["uniform_net_usdt"] for r in matched),
            "matched_overlay_net_usdt": sum(r["overlay_net_usdt"] for r in matched),
            "overlay_all_net_usdt": sum(r["overlay_net_usdt"] for r in rows),
        }
    save(
        Path(__file__).with_name("attribution.json"),
        {
            "result_sha256": sha(study.RESULT),
            "source_sha256": sha(Path(__file__)),
            "scope": "Matched entries on separate original and candidate native paths.",
            "accounts": accounts,
        },
    )


if __name__ == "__main__":
    main()
