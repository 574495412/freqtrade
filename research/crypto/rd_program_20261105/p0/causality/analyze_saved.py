#!/usr/bin/env python3
"""Read saved diagnostics and execute isolated source-method counterexamples.

No engine import, strategy instantiation, market replay, account access, or network.
This does NOT run lookahead-analysis and does NOT resolve the six historical alerts.
The original source is parsed with ast; selected method bodies are unmodified.
Only the requested report is written.  All source inputs remain read-only.
"""
from __future__ import annotations

import argparse
import ast
import copy
import csv
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[5]
STRATEGY = ROOT / "deploy/strategy/nfi-x8/NostalgiaForInfinityX8.py"
STRATEGY_SHA = "fc5cb221f29ebca5f336429d97920eb93917cdd87f9769381b23b467ca6d5bf3"
SAVED = ROOT / "user_data/crypto_quant"
FUTURES = SAVED / "nfi_x8_lookahead_inspect_20261105_v002"
SPOT = SAVED / "nfi_x8_spot_lookahead_check_20261105_v001"
FUNDING = ROOT / "docs/research-20261105/crypto-wide-comparison-20261105"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def receipt(path: Path) -> dict:
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def extracted_class(path: Path, class_name: str, methods: list[str], namespace: dict):
    """Compile only named, unchanged AST method nodes, with deferred annotations."""
    tree = ast.parse(path.read_text())
    source_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    selected = [copy.deepcopy(n) for n in source_class.body if isinstance(n, ast.FunctionDef) and n.name in methods]
    if {n.name for n in selected} != set(methods):
        raise ValueError("Missing method; source contract changed")
    cls = ast.ClassDef(name="Extracted", bases=[], keywords=[], body=selected, decorator_list=[])
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), cls], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), namespace)
    return namespace["Extracted"], {
        n.name: {"start_line": n.lineno, "end_line": n.end_lineno,
                 "ast_sha256": hashlib.sha256(ast.dump(n, include_attributes=False).encode()).hexdigest()}
        for n in selected
    }


def source_constants() -> dict:
    tree = ast.parse(STRATEGY.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "NostalgiaForInfinityX8")
    values = {}

    def literal(node):
        if isinstance(node, ast.Name):
            return values[node.id]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return literal(node.left) + literal(node.right)
        return ast.literal_eval(node)

    for node in cls.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    try:
                        values[target.id] = literal(node.value)
                    except (ValueError, TypeError, KeyError):
                        pass
    return values


def source_method_proofs(captures: list, cache: dict) -> dict:
    methods = ["is_backtest_mode", "order_filled", "_remove_profit_target", "exit_profit_target"]
    cls, method_receipts = extracted_class(STRATEGY, "NostalgiaForInfinityX8", methods,
                                         {"datetime": datetime, "timedelta": timedelta})
    constants = source_constants()

    def instance(mode: str, data: dict):
        obj = cls()
        obj.dp = SimpleNamespace(runmode=SimpleNamespace(value=mode))
        obj.target_profit_cache = SimpleNamespace(data=copy.deepcopy(data), save=lambda: None)
        for key in ["system_name_use", "system_v3_2_name", "system_v3_1_name", "system_v3_name",
                    "system_v4_name", "derisk_enable", "long_rapid_mode_tags", "long_rebuy_mode_tags",
                    "long_rebuy_grind_mode_tags", "long_scalp_mode_tags", "long_scalp_rebuy_grind_mode_tags",
                    "short_scalp_mode_tags"]:
            setattr(obj, key, constants[key])
        obj.is_futures_mode = True
        # System flags are unused in the selected profit_max branches below.
        obj.get_system_version_flags = lambda trade: (False, False, False)
        obj.is_system_v4 = lambda trade: True
        return obj

    pair = "ADA/USDT:USDT"
    cleanup_cases = []
    for mode, is_open in [("util_no_exchange", True), ("backtest", True), ("hyperopt", True), ("dry_run", False)]:
        obj = instance(mode, {pair: {"profit": 0.05}})
        trade = SimpleNamespace(is_open=is_open, amount=100.0, entry_side="buy", exit_side="sell",
                                nr_of_successful_entries=1, nr_of_successful_exits=1,
                                set_custom_data=lambda **kwargs: None, select_filled_orders=lambda *args: [])
        order = SimpleNamespace(ft_order_side="sell", safe_amount_after_fee=100.0,
                                ft_order_tag="exit_profit_long_tc_t_1_1_m ( 145 )")
        obj.order_filled(pair, trade, order, dt("2025-01-20T03:05:00+00:00"))
        cleanup_cases.append({"runmode": mode, "trade_is_open_at_callback": is_open,
                              "recognized_as_backtest": obj.is_backtest_mode(),
                              "pair_cache_removed": pair not in obj.target_profit_cache.data})
    assert [x["pair_cache_removed"] for x in cleanup_cases] == [False, True, True, True]

    target_cases = []
    for capture in captures:
        baseline = capture["baseline"]
        matching = [t for t in capture["slices"]["exit"]["trades"]
                    if t["pair"] == baseline["pair"] and t["open_date"] == baseline["open_date"]]
        if not matching or matching[0]["close_date"] == baseline["close_date"]:
            continue
        trade_json = matching[0]
        assert len(trade_json["orders"]) == 2  # This proof is deliberately bounded.
        first, last = trade_json["orders"]
        side = -1 if trade_json["is_short"] else 1
        entry_cost = first["amount"] * first["safe_price"]
        exit_cost = last["amount"] * last["safe_price"]
        profit = side * (exit_cost - entry_cost) - entry_cost * trade_json["fee_open"] - exit_cost * trade_json["fee_close"] + trade_json["funding_fees"]
        profit_init_ratio = profit / entry_cost
        mode_name = "short_rebuy" if trade_json["is_short"] else "long_tc"
        old_cache = cache[trade_json["pair"]]
        obj = instance("util_no_exchange", {})
        trade = SimpleNamespace(leverage=1.0, is_short=trade_json["is_short"],
                                select_filled_orders=lambda *args: [])
        # These indicator values only satisfy eagerly read fields; the target
        # branches hit by these three 0.1%-2% samples do not depend on them.
        candle = {k: 0.0 for k in ["RSI_14", "CMF_20", "CMF_20_1h", "CMF_20_4h", "ROC_9_4h"]}
        args = [mode_name, trade_json["pair"], trade, dt(trade_json["close_date"]),
                trade_json["close_rate"], profit, profit_init_ratio, profit_init_ratio,
                profit_init_ratio, candle, candle, old_cache["rate"]]
        cached_result = obj.exit_profit_target(*args, old_cache["profit"], old_cache["sell_reason"],
                                              dt(old_cache["time_profit_reached"]), trade_json["enter_tag"].split())
        # Same call and timestamps; only the previous profit is changed to the
        # current profit, i.e. no assumed earlier peak. This is a method-level
        # counterfactual, not a reconstructed historical clean-cache replay.
        neutral_result = obj.exit_profit_target(*args, profit_init_ratio, old_cache["sell_reason"],
                                               dt(old_cache["time_profit_reached"]), trade_json["enter_tag"].split())
        expected = trade_json["exit_reason"].split(" (", 1)[0].removesuffix("_m")
        assert cached_result == (True, expected)
        assert neutral_result == (False, None)
        target_cases.append({"pair": trade_json["pair"], "open_date": trade_json["open_date"],
                             "saved_slice_exit": trade_json["close_date"], "profit_init_ratio": profit_init_ratio,
                             "final_saved_cache": old_cache,
                             "cache_time_after_slice_exit": dt(old_cache["time_profit_reached"]) > dt(trade_json["close_date"]),
                             "with_final_saved_peak": list(cached_result), "with_current_profit_as_peak": list(neutral_result),
                             "scope": "unchanged isolated source method; sufficient mechanism, not proof this exact cache was read in the old run"})
    assert len(target_cases) == 3

    order_cls, order_receipt = extracted_class(ROOT / "freqtrade/persistence/trade_model.py", "Order", ["to_json"],
        {"UTC": UTC, "DATETIME_PRINT_FORMAT": "%Y-%m-%d %H:%M:%S", "dt_ts_none": lambda x: int(x.timestamp() * 1000) if x else None})
    orders = []
    for created in ["2025-08-10T15:55:00+00:00", "2025-08-10T16:00:00+00:00"]:
        order = order_cls()
        for key, val in dict(safe_amount=1.0, safe_price=1.0, ft_order_side="buy", order_filled_utc=dt("2025-08-10T16:00:00+00:00"),
                             ft_order_tag="entry", cost=1.0008, ft_pair="CRV/USDT:USDT", order_id="example", status="closed",
                             average=1.0, filled=1.0, ft_is_open=False, order_date=dt(created),
                             order_filled_date=dt("2025-08-10T16:00:00+00:00"), order_type="limit", price=1.0,
                             remaining=0.0, ft_fee_base=0.0, funding_fee=0.0).items():
            setattr(order, key, val)
        orders.append(order)
    minified = [x.to_json("buy", minified=True) for x in orders]
    full = [x.to_json("buy", minified=False) for x in orders]
    assert minified[0] == minified[1] and full[0]["order_timestamp"] != full[1]["order_timestamp"]
    return {"evidence_level": "A_source_methods_and_synthetic_counterexamples_not_market_replay",
            "method_receipts": method_receipts, "exit_cleanup_mode_counterexample": cleanup_cases,
            "saved_peak_exit_counterexamples": target_cases,
            "minified_order_information_loss": {"source_method": order_receipt,
                "same_fill_different_creation_times": [x["order_date"] for x in full],
                "minified_outputs_identical": True, "minified_fields": sorted(minified[0]),
                "conclusion": "order creation time is not identifiable from this export; do not fabricate same-bar branch selection"}}


def analyze() -> dict:
    if sha(STRATEGY) != STRATEGY_SHA:
        raise ValueError("NFI fixed source hash mismatch")
    captures_file = FUTURES / "captured-slices.json"
    captures = json.loads(captures_file.read_text())
    process_file = FUTURES / "process.json"
    process = json.loads(process_file.read_text())
    if sha(captures_file) != process["captured-slices.json_sha256"]:
        raise ValueError("Saved capture differs from completed receipt")
    caches = {
        "futures": FUTURES / "nfix8-profit_max-cz-nfi-x8-v18-0-146-baseline-binance-USDT.json",
        "spot": SPOT / "nfix8-profit_max-cz-nfi-x8-spot-36-paper-20261105-binance-USDT.json",
    }
    futures_cache = json.loads(caches["futures"].read_text())
    input_paths = [STRATEGY, captures_file, process_file, *caches.values(),
                   FUTURES / "lookahead-result.csv", SPOT / "lookahead-result.csv", SPOT / "lookahead.log", SPOT / "process.json",
                   FUNDING / "funding-event-boundary-audit.json", FUNDING / "funding-timing-bound-review.json",
                   ROOT / "freqtrade/commands/optimize_commands.py", ROOT / "freqtrade/optimize/analysis/lookahead.py",
                   ROOT / "freqtrade/optimize/backtesting.py", ROOT / "freqtrade/persistence/trade_model.py"]
    alerts = []
    for item in captures:
        b = item["baseline"]
        exit_trades = item["slices"]["exit"]["trades"]
        # Reproduce the fixed official tool's timestamp-only predicate exactly.
        if any(t["close_date"] == b["close_date"] for t in exit_trades):
            continue
        same = [t for t in exit_trades if t["pair"] == b["pair"] and t["open_date"] == b["open_date"]]
        if len(same) != 1:
            raise ValueError("Unexpected trade identity ambiguity")
        cut = same[0]
        overlapping = [x["baseline"]["pair"] for x in captures if x["baseline"]["pair"] != b["pair"]
                       and dt(x["baseline"]["open_date"]) <= dt(cut["close_date"]) < dt(x["baseline"]["close_date"])]
        alerts.append({"id": f"futures_{item['index']}", "market": "futures", "pair": b["pair"],
            "open_date": b["open_date"], "baseline_close_date": b["close_date"], "baseline_exit_reason": b["exit_reason"],
            "slice_close_date": cut["close_date"], "slice_exit_reason": cut["exit_reason"],
            "baseline_order_count": len(b["orders"]), "slice_order_count": len(cut["orders"]),
            "first_fill_identical": b["orders"][0] == cut["orders"][0],
            "other_captured_baseline_positions_open_at_early_exit": overlapping,
            "overlap_scope": "saved 12 baseline trades only; not assertion of all internal state",
            "status": "mechanism_identified_not_yet_attributed_by_controlled_replay",
            "missing_evidence": ["cache contents immediately before each old baseline/slice", "callback inputs at first path divergence", "clean phase-isolated replay"],
            "interpretation": "persistent cache/time reversal fits the exit path; neither full causality pass nor normal-live future-data reading established"})
    spot_log = (SPOT / "lookahead.log").read_text()
    pattern = re.compile(r"found lookahead-bias in trade pair: (\S+), timerange:(.*?) - (.*?), idx: (\d+)")
    for pair, start, end, idx in pattern.findall(spot_log):
        alerts.append({"id": f"spot_{idx}", "market": "spot", "pair": pair, "open_date": start,
                       "baseline_close_date": end, "slice_close_date": None, "slice_exit_reason": None,
                       "status": "blocked_old_spot_slices_not_exported",
                       "missing_evidence": ["old spot per-slice trade output", "pre-phase cache state", "first-divergence callback trace"],
                       "interpretation": "same source lifecycle mechanism applies, but old spot alert attribution remains unproven"})
    if len(alerts) != 6:
        raise ValueError("Expected exactly three saved alerts per market")
    funding = json.loads((FUNDING / "funding-event-boundary-audit.json").read_text())
    sensitivity = json.loads((FUNDING / "funding-timing-bound-review.json").read_text())
    return {
        "schema_version": 1, "scope": "P0_CAUSALITY saved evidence and source-method proofs only",
        "orders_enabled": False, "new_backtest_invocations": 0, "new_official_diagnostic_commands": 0,
        "script_sha256": sha(Path(__file__)), "input_receipts": [receipt(p) for p in input_paths],
        "saved_futures_capture_count": len(captures), "saved_futures_slice_count": sum(len(x["slices"]) for x in captures),
        "alerts": alerts, "source_method_proofs": source_method_proofs(captures, futures_cache),
        "funding_event_status": {
            "status": "BLOCKED_NON_HOURLY_EVENT_ORDERING_AND_ACTUAL_PUBLICATION_TIMES",
            "account_summaries": [{k: v for k, v in x.items() if k not in {"events", "trades"}} for x in funding["accounts"]],
            "conditional_sensitivity_only": [{"account": x["account"], "event_count": x["event_count"],
                "abs_component_sum_usdt": x["abs_component_sum_usdt"]} for x in sensitivity["accounts"]],
            "not_proven": ["actual exchange funding settlement", "5m mark NAV", "all intermediate native funding state", "complete NAV/drawdown error bound", "funding rate publication/arrival time"],
            "required_future_trace": ["order create/fill/cancel timestamps and IDs", "callback sequence number", "pre/post amount", "pre/post funding_fee_running and funding_fees", "per-order funding attribution", "funding source timestamp/publication/arrival", "same-time deterministic event priority"]},
        "decision": "No original alert is declared resolved by these static/synthetic proofs; native strategy remains causality-gated pending registered controlled diagnostics."
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New report file; refusal to overwrite")
    args = parser.parse_args()
    result = json.dumps(analyze(), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        with args.output.open("x") as handle:
            handle.write(result)
        print(json.dumps({"report": str(args.output), "sha256": sha(args.output), "new_backtests": 0}))
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
