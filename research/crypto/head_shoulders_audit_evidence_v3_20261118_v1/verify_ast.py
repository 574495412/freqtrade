"""Prove financial/source functions and account numeric body unchanged from v2."""

import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / "head_shoulders_financial_native_20261117_v3/independent_account_audit_v2.py"
NEW = HERE / "independent_account_audit_v3.py"


def dump(node):
    return ast.dump(node, include_attributes=False)


def call_named(node, name):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name


class RemoveEvidence(ast.NodeTransformer):
    def visit_Assign(self, node):
        if any(isinstance(t, ast.Name) and t.id == "conditional_scope" for t in node.targets):
            assert isinstance(node.value, ast.IfExp) and call_named(node.value.body, "load_conditional_scope")
            return None
        return self.generic_visit(node)

    def visit_Expr(self, node):
        if call_named(node.value, "verify_scope_coverage"):
            return None
        return self.generic_visit(node)

    def visit_If(self, node):
        if len(node.body) == 1 and isinstance(node.body[0], ast.Assign) and call_named(node.body[0].value, "supplement_boundary_evidence"):
            assert dump(node.test) == dump(ast.parse("not hs", mode="eval").body) and not node.orelse
            return None
        return self.generic_visit(node)

    def visit_Dict(self, node):
        keep = []
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "boundary_evidence_supplement":
                assert call_named(value, "account_evidence_fields")
            else:
                keep.append((key, value))
        node.keys = [k for k, _ in keep]
        node.values = [v for _, v in keep]
        return self.generic_visit(node)


class NormalizeMainPaths(ast.NodeTransformer):
    def visit_Name(self, node):
        if node.id == "BASE_STUDY":
            node.id = "HERE"
        return node

    def visit_Constant(self, node):
        replacements = {"independent_audit_freeze_v3.json": "independent_audit_freeze_v2.json", "independent_audit_outputs_v3": "independent_audit_outputs"}
        if isinstance(node.value, str) and node.value in replacements:
            node.value = replacements[node.value]
        return node


def check():
    old = {n.name: n for n in ast.parse(OLD.read_text()).body if isinstance(n, ast.FunctionDef)}
    new = {n.name: n for n in ast.parse(NEW.read_text()).body if isinstance(n, ast.FunctionDef)}
    assert old.keys() == new.keys()
    identical = []
    for name in old:
        if name not in {"account", "main"}:
            assert dump(old[name]) == dump(new[name]), name
            identical.append({"function": name, "ast_sha256": hashlib.sha256(dump(old[name]).encode()).hexdigest()})
    assert dump(old["account"]) == dump(RemoveEvidence().visit(deepcopy(new["account"])))
    assert dump(old["main"]) == dump(NormalizeMainPaths().visit(deepcopy(new["main"])))
    required = {"cash_from_orders", "funding_history", "two_fill_funding", "trade_path", "metrics", "periods_for", "standalone_gate", "fixed_combo", "boundary_evidence", "source_trade_check", "source_terminal", "missing_exposure"}
    assert required <= {v["function"] for v in identical}
    return {"passed": True, "old_file_sha256": hashlib.sha256(OLD.read_bytes()).hexdigest(), "new_file_sha256": hashlib.sha256(NEW.read_bytes()).hexdigest(), "unchanged_functions": identical, "account_after_removing_only_evidence_additions_AST_equal": True, "main_after_only_freeze_base_and_output_path_changes_AST_equal": True, "all_numeric_formulas_tolerances_and_economic_gates_unchanged": True, "strict_HS_sequence_unchanged": True}


if __name__ == "__main__":
    result = check()
    (HERE / "ast_equivalence.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"passed": True, "unchanged_functions": len(result["unchanged_functions"])}))
