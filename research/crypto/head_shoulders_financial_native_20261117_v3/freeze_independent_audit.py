"""Freeze independent audit rules before synthetic cases or new financial outputs."""

import ast
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]
VENDOR = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reference(path):
    return {"path": str(path), "sha256": sha(path)}


def main():
    assert not (HERE / "independent_audit_freeze.json").exists()
    plan = json.loads((HERE / "plan.json").read_text())
    assert not (HERE / "execution_release.json").exists()
    assert not any(
        (Path(plan["output_root"]) / c["name"] / "result.json").exists() for c in plan["candidates"]
    )
    source = HERE.parent / "nfi40_allocation_native_20261117_v1/audit_actual500.py"
    trees = [ast.parse(p.read_text()) for p in (source, HERE / "independent_account_audit.py")]
    preserved = {}
    for name in ("metrics", "funding_history"):
        nodes = [
            next(n for n in t.body if isinstance(n, ast.FunctionDef) and n.name == name)
            for t in trees
        ]
        assert ast.dump(nodes[0]) == ast.dump(nodes[1])
        preserved[name] = hashlib.sha256(ast.dump(nodes[0]).encode()).hexdigest()
    protocol = {
        "schema": "hs40-independent-account-audit.v1",
        "frozen_at": datetime.now(UTC).isoformat(),
        "new_hs_financial_outputs_observed": False,
        "synthetic_tests_run_before_freeze": False,
        "native_financial_start_authorized": False,
        "primary_result_owner": "Pinned official Freqtrade matching/wallets/funding; this is a read-only independent auditor, never a substitute account engine",
        "execution_extension": "LegacyFiveMinutePrecisionMixin is a local research override of supported get_pair_precision, preserving original NFI5m inferred whole-month table/asof. It is not upstream functionality, historical-rule certification or a lookahead repair.",
        "official_vendor_files_unmodified": 595,
        "source_catalog": reference(
            HERE.parent
            / "head_shoulders_policy_native_20261117_v2/native_probe/policy_event_catalog.parquet"
        ),
        "input_validation": reference(HERE / "input_validation.json"),
        "precision_validation": reference(HERE / "preflight_attempt2.json"),
        "source_event_proof": "Compare all 1891 IDs and full immutable metadata to sealed pre-financial v2 catalog; independently calculate source exits only from source closes available by each actual exit clock. Catalog is identity evidence, not a signal cache for the native account.",
        "source_exit_rules": {
            "inputs": "log(close), right-shoulder stop, neck_end +/- head_height target",
            "crossing": "strict >/<; equality is not a crossing; target then stop then expiry",
            "expiry": "break_i+head_width-1 source bar; available at its open+1h",
            "entry": "one event only, exact confirmation+1h; self-terminal known flat; no later reentry",
            "exit_clock": "first source terminal available time equals actual source close_timestamp on native5m grid",
            "risk_priority": "Pinned IStrategy custom source exit is appended before stop_loss/liquidation. Risk at or after a visible source exit is rejected under this fixed no-pending source path.",
            "force_exit": "Separate native terminal type, exact final base-row2026-08-31T23Z, no previously available source terminal",
            "native_stop_liquidation_price_certified_by_this_auditor": False,
        },
        "funding_model": {
            "route": "Unchanged original native inclusive open<=event<=close, hourly refresh and close_bt_order transfer; not venue ownership or Hawkes before/after-fill branches",
            "replay": "AST-preserved independent NFI500 hourly and order-transfer replay; every terminal campaign funding and every before-clock NAV reconciled",
            "strict_hs_oracle": "Exactly2 exported orders, one entry then equal full exit, closed native trade, initial fill==open_timestamp==source availability, no DCA, complete fixed1h/5m clocks, integer-hour funding. Terminal sign*q*sum(E<=F<=C); snapshot sign*q*sum(E<=F<T) before fill/refresh at T.",
            "minified_schema": "BT_DATA_COLUMNS drops open_fill_timestamp/nr_success/has_open_orders; validate if present, do not fabricate. Closed native trade plus exactly2 equal entry/exit orders and owner lifecycle proves fills; pending open orders are included by owner exporter and would break the2-order path.",
            "boundary_unknown": "Outside strict H&S proof, missing/deferred boundary order creation timestamps => UNKNOWN regardless of matching aggregate NAV/funding. NFI DCA is not presumed unambiguous.",
            "force_tail_scope": "Only this fixed final whole-hour base row after complete5m traversal; no generalization to arbitrary tails",
            "missing_slot": "AXS and TRB2026-06-24T04Z: gross quantity before/after every same-slot fill per campaign; any nonzero exposure UNKNOWN; never impute zero or net opposite campaigns/sleeves",
        },
        "account_contract": {
            "capitals": [1000, 500],
            "cost_bps_each_side": [15, 8],
            "pool_size": 40,
            "leverage": 1,
            "slots_per_wallet": 6,
            "balance_ratio": 0.99,
            "full_window_points": 14592,
            "2025_points": 8761,
            "2026_through_august_points": 5832,
            "window": [plan["start"], plan["comparison_end"]],
            "nav": "capital + closed fill cash + open mark-open value - both filled commissions - remaining exit fee reserve + native accrued funding; observe before fills and funding refresh at each hourlyclock",
            "periods": plan["periods"],
            "slices_reset_capital": False,
            "terminal_force_balance_used_for_ranking": False,
        },
        "tolerances_usdt": {
            "hourly_nav": 3e-5,
            "campaign_cash": 1e-5,
            "aggregate_cash": 1e-4,
            "funding": 1e-7,
        },
        "combo": "Actual HS500 + independently reaudited actual NFI500 levels =1000; no linear scaling, no rebalance/weight search. Both costs, all complete periods, same-cost frozen originalNFI1000 benchmark; both sleeves VERIFIED required.",
        "gates_owner": reference(HERE / "economic_protocol.json"),
        "unknown_policy": "Preserve numerical conditional metrics and UNKNOWN; never upgrade from aggregate equality. Invalid source/cash/schema checks fail, retained for diagnosis without retuning.",
        "preflight_scope": "v3 preparation probe entry_masks all0; v2 38callback cases synthetic. Neither establishes actual market matching trajectories.",
        "independent_OOS": False,
        "historical_rules_certified": False,
        "orders_enabled": False,
        "preserved_NFI500_AST_functions": preserved,
        "NFI500_audit_source": reference(source),
        "cash_equation_reference": reference(
            Path(
                "/Users/chrise/GitHub/TradingAgents/research_tools/nfi_reproduction/audit_original.py"
            )
        ),
    }
    (HERE / "independent_audit_protocol.json").write_text(json.dumps(protocol, indent=2) + "\n")
    owners = [
        VENDOR / p
        for p in (
            "freqtrade/optimize/backtesting.py",
            "freqtrade/persistence/trade_model.py",
            "freqtrade/strategy/interface.py",
            "freqtrade/exchange/exchange.py",
            "freqtrade/data/btanalysis/bt_fileutils.py",
        )
    ]
    files = [
        HERE / n
        for n in (
            "independent_account_audit.py",
            "test_independent_audit.py",
            "freeze_independent_audit.py",
            "independent_audit_protocol.json",
            "plan.json",
            "economic_protocol.json",
            "preparation_manifest.json",
            "input_validation.json",
            "preflight_attempt2.json",
        )
    ]
    files += owners + [
        source,
        Path(protocol["source_catalog"]["path"]),
        Path(protocol["cash_equation_reference"]["path"]),
    ]
    result = {
        "frozen_at": datetime.now(UTC).isoformat(),
        "new_financial_outputs_observed": False,
        "synthetic_tests_executed": False,
        "files": {str(p): sha(p) for p in files},
    }
    (HERE / "independent_audit_freeze.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "frozen_files": len(files),
                "freeze_sha256": sha(HERE / "independent_audit_freeze.json"),
                "financial_start_called": False,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
