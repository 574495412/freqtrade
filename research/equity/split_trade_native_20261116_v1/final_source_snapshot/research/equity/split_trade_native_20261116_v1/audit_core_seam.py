"""Verify that the local native core changes only trade construction dispatch."""

import ast
import difflib

from bootstrap import HERE, ROOT, save, sha


def main():
    before = HERE / "before/freqtrade/optimize/backtesting.py"
    after = ROOT / "freqtrade/optimize/backtesting.py"
    original = ast.parse(before.read_text())
    changed = ast.parse(after.read_text())
    cls = next(n for n in changed.body if isinstance(n, ast.ClassDef) and n.name == "Backtesting")
    factory = next(
        n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_create_trade"
    )
    returned = factory.body[-1]
    assert isinstance(returned, ast.Return)
    assert ast.unparse(returned.value) == "LocalTrade(**kwargs)"
    cls.body.remove(factory)
    matches = []
    for node in ast.walk(changed):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr == "_create_trade"
        ):
            matches.append(node)
            node.func = ast.Name(id="LocalTrade", ctx=ast.Load())
    assert len(matches) == 1
    assert ast.dump(original) == ast.dump(changed)
    (HERE / "core_extension.patch").write_text(
        "".join(
            difflib.unified_diff(
                before.read_text().splitlines(True),
                after.read_text().splitlines(True),
                fromfile="upstream/freqtrade/optimize/backtesting.py",
                tofile="local/freqtrade/optimize/backtesting.py",
            )
        )
    )
    save(
        HERE / "core_seam_audit.json",
        {
            "passed": True,
            "original_core_sha256": sha(before),
            "local_core_sha256": sha(after),
            "changed_call_sites": 1,
            "new_methods": 1,
            "all_other_native_ast_exact": True,
            "default_factory": "LocalTrade(**kwargs)",
            "scope": (
                "AST equality plus explicit native fixture financial parity; "
                "not all strategy execution modes"
            ),
        },
    )


if __name__ == "__main__":
    main()
