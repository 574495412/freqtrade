"""Verify native signal parity and event-contract tests before financial runs."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

from research.crypto.session_carry_native_20261113_v2.preflight import funding
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / "session_carry_native_20261113_v2"


def main():
    plan = json.loads((HERE / "plan.json").read_text())
    spec = importlib.util.spec_from_file_location("ownership_runner", HERE / "native_runner.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    for candidate in plan["candidates"]:
        runner.verify(plan, candidate)
    result = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", str(HERE / "test_funding_ownership.py")],
        capture_output=True,
        text=True,
    )
    save(
        HERE / "native_fixture_receipt.json",
        {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr},
    )
    if result.returncode:
        raise ValueError("Native funding fixtures failed")
    # Existing source and funding-admission checks are reused explicitly;
    # rerun the raw archive checks but save into this new campaign only.
    original_here = funding.__globals__["HERE"]
    funding.__globals__["HERE"] = HERE
    try:
        funding(plan)
    finally:
        funding.__globals__["HERE"] = original_here
    sys.path.insert(0, plan["engine_source"])
    from freqtrade.resolvers import StrategyResolver

    original_plan = json.loads((OLD / "plan.json").read_text())
    original_output = Path(original_plan["output_root"])
    memo, rows = {}, []
    for candidate in plan["candidates"]:
        key = candidate["original_candidate"]
        source_path = original_output / f"preflight_{key}.parquet"
        if key not in memo:
            source = pd.read_parquet(source_path)
            pair = candidate["pairs"][0]
            config = json.loads(Path(candidate["config"]["path"]).read_text())
            config.update(strategy_path=str(HERE), user_data_dir=Path(plan["output_root"]))
            strategy = StrategyResolver.load_strategy(config)
            price = source[["date", "open", "high", "low", "close", "volume"]].copy()
            actual = strategy.advise_exit(
                strategy.advise_entry(
                    strategy.advise_indicators(price, {"pair": pair}), {"pair": pair}
                ),
                {"pair": pair},
            )
            pd.testing.assert_frame_equal(actual, source, check_exact=True)
            memo[key] = len(source)
        target = Path(plan["output_root"]) / f"preflight_{candidate['name']}.parquet"
        if not target.exists():
            os.link(source_path, target)
        if sha(target) != sha(source_path):
            raise ValueError("Preflight hardlink differs")
        rows.append(
            {
                "candidate": candidate["name"],
                "native_signal_rows_exact": memo[key],
                "source_boundary_prefix_checks_reused_not_new": True,
            }
        )
    save(
        HERE / "preflight.json",
        {
            "passed": True,
            "records": rows,
            "unique_native_signal_recalculations": len(memo),
            "fixture_tests": 7,
            "independent_oos_certified": False,
        },
    )
    print(
        json.dumps(
            {
                "preflight_passed": True,
                "unique_native_strategy_recalculations": len(memo),
                "native_funding_tests": 7,
            }
        )
    )


if __name__ == "__main__":
    main()
