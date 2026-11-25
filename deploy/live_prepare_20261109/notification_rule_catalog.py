"""Build a small notification reference from the pinned NFI strategy source."""

import ast
import hashlib
import json
import re
from pathlib import Path


STRATEGY_SOURCE = Path(__file__).resolve().parents[1] / "strategy/nfi-x8/NostalgiaForInfinityX8.py"
CATALOG = Path(__file__).with_name("notification_rule_catalog.json")


def build_catalog(path):
    source = path.read_bytes()
    lines = source.decode().splitlines()
    rules = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        match = re.fullmatch(r"(long|short)_entry_condition_index == (\d+)", ast.unparse(node.test))
        if not match:
            continue
        side, tag = match.groups()
        statement = node.body[-1]
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            raise ValueError(f"Unexpected NFI entry rule structure: {tag}")
        call = statement.value
        if ast.unparse(call.func) != f"{side}_entry_logic.append" or len(call.args) != 1:
            raise ValueError(f"Unexpected NFI entry rule expression: {tag}")
        if statement.end_lineno - statement.lineno > 25:
            raise ValueError(f"Unbounded NFI entry rule expression: {tag}")
        rules[tag] = {
            "side": side,
            "line": statement.lineno,
            "gate": ast.unparse(call.args[0]),
            "label": lines[node.lineno - 2].strip().removeprefix(f"# Condition #{tag} - "),
        }
    if not rules:
        raise ValueError("No pinned NFI entry rules found")
    return {"source_sha256": hashlib.sha256(source).hexdigest(), "entries": rules}


if __name__ == "__main__":
    CATALOG.write_text(
        json.dumps(build_catalog(STRATEGY_SOURCE), ensure_ascii=False, indent=2) + "\n"
    )
