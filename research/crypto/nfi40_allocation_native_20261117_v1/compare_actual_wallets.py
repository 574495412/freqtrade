"""Post-hoc attribution of two actual native ledgers; no hypothetical wallet."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile


HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def ledger(archive, expected_sha, capital):
    if sha(archive) != expected_sha:
        raise ValueError("Native archive changed")
    with ZipFile(archive) as zipped:
        if zipped.testzip() is not None:
            raise ValueError("Native archive CRC failed")
        payloads = [json.loads(zipped.read(n)) for n in zipped.namelist() if n.endswith(".json") and not n.endswith("_config.json")]
    stats = next(p["strategy"]["NostalgiaForInfinityX8"] for p in payloads if "strategy" in p)
    trades = {}
    for index, trade in enumerate(stats["trades"]):
        key = (trade["pair"], trade["is_short"], trade["open_timestamp"], trade["enter_tag"])
        if key in trades or trade["is_open"] or trade["leverage"] != 1:
            raise ValueError("Ambiguous campaign identity or unsupported account")
        trades[key] = {"index": index, "trade": trade}
    total = sum((Decimal(str(row["trade"]["profit_abs"])) for row in trades.values()), Decimal(0))
    if abs(total - Decimal(str(stats["profit_total_abs"]))) > Decimal("0.000001"):
        raise ValueError("Native campaign totals disagree")
    return trades, total / capital


def main():
    baseline_contract = read(HERE.parent / "mainline_comparison_contract_20261117_v1/contract.json")
    baseline = next(b for b in baseline_contract["baselines"] if b["cost_bps_per_side"] == 15)
    old_archive = next(r for r in baseline["old_native_outputs_rechecked"] if r["path"].endswith(".zip"))
    observation = read(HERE / "first_completed_account_observation.json")
    plan = read(HERE / "plan.json")
    actual = Path(plan["output_root"]) / observation["candidate"]
    receipt = read(actual / "process_receipt.json")
    if receipt["returncode"] != 0 or receipt["result_sha256"] != sha(actual / "result.json"):
        raise ValueError("Actual500 process must be complete")
    archives = list((actual / "native_results").glob("*.zip"))
    if len(archives) != 1:
        raise ValueError("Exactly one actual500 archive required")
    old, old_return = ledger(Path(old_archive["path"]), old_archive["sha256"], Decimal(1000))
    new, new_return = ledger(archives[0], sha(archives[0]), Decimal(500))
    groups = {name: {"campaigns": 0, "original_normalized_net": Decimal(0), "actual500_normalized_net": Decimal(0)} for name in ("matched_entry_identity", "only_original1000", "only_actual500")}
    rows = []
    for key in sorted(set(old) | set(new), key=lambda k: (k[2], k[0], str(k[3]), k[1])):
        a, b = old.get(key), new.get(key)
        group = "matched_entry_identity" if a and b else "only_original1000" if a else "only_actual500"
        old_pnl = Decimal(str(a["trade"]["profit_abs"])) / 1000 if a else Decimal(0)
        new_pnl = Decimal(str(b["trade"]["profit_abs"])) / 500 if b else Decimal(0)
        groups[group]["campaigns"] += 1
        groups[group]["original_normalized_net"] += old_pnl
        groups[group]["actual500_normalized_net"] += new_pnl
        rows.append({"entry_identity": key, "group": group, "original_trade_index": a["index"] if a else None, "actual500_trade_index": b["index"] if b else None, "original_contribution": str(old_pnl), "actual500_contribution": str(new_pnl), "original_minus_actual500_percentage_points": str((old_pnl - new_pnl) * 100)})
    total_gap = sum((g["original_normalized_net"] - g["actual500_normalized_net"] for g in groups.values()), Decimal(0))
    if abs(total_gap - (old_return - new_return)) > Decimal("1e-20"):
        raise ValueError("Exclusive attribution groups do not sum")
    value = {
        "purpose": "POST_HOC_DESCRIPTIVE_ACTUAL_LEDGER_ATTRIBUTION_ONLY",
        "entry_key": ["pair", "is_short", "open_timestamp", "exact_enter_tag"],
        "cost_bps_per_side": 15,
        "source_archives": [old_archive, {"path": str(archives[0]), "sha256": sha(archives[0])}],
        "original_native_terminal_return": str(old_return), "actual500_native_terminal_return": str(new_return),
        "original_minus_actual500_percentage_points": str(total_gap * 100),
        "native_terminal_minus_common_mark_return": {"original1000": str(old_return - Decimal(str(baseline["net_return"]))), "actual500": str(new_return - Decimal(str(observation["recomputed_from_actual_curve"]["net_return"])))},
        "exclusive_groups": groups, "rows": rows,
        "limitations": ["Matching entry identities do not prove identical subsequent DCA or exit paths", "No rejection receipts: no causal attribution to minimum lots or slot constraints", "Terminal ledger and common marked endpoint remain distinct", "Funding boundary and historical execution limits retained"],
        "normalization_is_actual_profit_divided_by_own_initial_capital": True,
        "scaled_wallet_or_removed_campaign_counterfactual": False,
        "new_financial_accounts": 0, "strategy_selection_or_gate_changed": False, "goal_complete": False,
    }
    target = HERE / "actual_wallet_campaign_comparison_cost15.json"
    with target.open("x") as stream:
        json.dump(value, stream, indent=2, default=str)
        stream.write("\n")
    print(json.dumps({"exclusive_groups": groups, "gap_percentage_points": str(total_gap * 100), "report_sha256": sha(target)}, default=str))


if __name__ == "__main__":
    main()
