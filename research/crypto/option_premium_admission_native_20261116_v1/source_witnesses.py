"""Selected original notebook arithmetic on synthetic inputs;not its account."""

import ast
import json
from pathlib import Path

import pandas as pd


HERE = Path(__file__).absolute().parent


def main():
    path = HERE / "sources/08_variance_risk_premium.py"
    tree = ast.parse(path.read_text())
    wanted = {
        'roll["gross"]',
        'roll["entry_cost"]',
        'roll["hedge_cost"]',
        'roll["net"]',
        'roll["equity"]',
        "dd",
    }
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            ast.unparse(target) in wanted or ast.unparse(target).replace("'", '"') in wanted
            for target in node.targets
        )
    ]
    assert len(nodes) == 6
    code = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    outputs = []
    for scale in (100, 200):
        frame = pd.DataFrame(
            {"implied": [0.7, 0.7], "realised": [0.6, 0.6], "abs_move": [1.0, 1.0]}
        )
        values = {
            "roll": frame,
            "VEGA_SCALE": scale,
            "STRADDLE_COST_BPS": 150.0,
            "HEDGE_COST_BPS": 8.0,
        }
        exec(compile(code, str(path) + "::selected-arithmetic-only", "exec"), values)  # noqa: S102
        outputs.append(
            {
                "scale": scale,
                "gross": float(frame.gross.iloc[0]),
                "net": float(frame.net.iloc[0]),
                "equity_last": float(frame.equity.iloc[-1]),
            }
        )
    assert abs(outputs[0]["net"] - 11.87) < 1e-10
    assert outputs[1]["net"] == 2 * outputs[0]["net"]
    # Reuse the exact two original equity/DD statements,with fixed synthetic net losses.
    subset = [
        node
        for node in nodes
        if ast.unparse(node.targets[0]).replace("'", '"') in {'roll["equity"]', "dd"}
    ]
    loss = pd.DataFrame({"net": [-1.0, -2.0]})
    values = {"roll": loss}
    exec(  # noqa: S102 - only the two reviewed source assignments
        compile(
            ast.fix_missing_locations(ast.Module(body=subset, type_ignores=[])),
            str(path) + "::selected-drawdown-only",
            "exec",
        ),
        values,
    )
    assert values["dd"].tolist() == [0.0, -2.0]
    result = {
        "scope": "Synthetic source semantics,not financial strategies or native accounts",
        "scale_witnesses": outputs,
        "source_loss_drawdown": values["dd"].tolist(),
        "opening_zero_anchored_drawdown": [-1.0, -3.0],
        "normalised_notional_not_account_capital": True,
        "source_whole_notebook_or_network_executed": False,
        "funded_accounts": 0,
    }
    (HERE / "source_witnesses.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
