"""生成独立模拟运行包，拒绝把未验收候选包装成上线通过。"""

import argparse
import shutil
from pathlib import Path

from freqtrade.research.data import read, save, sha
from freqtrade.research.workflow import CANDIDATES, verify_freeze


def prepare(run: Path, dataset: Path, output: Path, diagnostic: str | None = None) -> dict:
    verify_freeze(run, dataset)
    evaluation = read(run / "evaluation.json")
    bias = read(run / "bias" / "result.json")
    historical_pass = evaluation["historical_economic_screen_passed"]
    bias_pass = bias["prefix"]["passed"] and bias["native_lookahead_passed"]
    if diagnostic is None and not (historical_pass and bias_pass):
        raise ValueError("候选未通过历史经济及泄漏验收；只能显式生成工程诊断包")
    chosen = diagnostic or evaluation["selection"]["agent"]
    if chosen not in CANDIDATES:
        raise ValueError("空仓或未知策略不能创建模拟交易包")
    output.mkdir(parents=True, exist_ok=False)
    strategy_dir = output / "strategies"
    strategy_dir.mkdir()
    strategy_source = Path(__file__).resolve().parents[2] / "freqtrade/research/strategies.py"
    shutil.copyfile(strategy_source, strategy_dir / "strategies.py")
    settings = read(run / "train/cost_3bps/settings.json")
    for key in (
        "strategy_list",
        "timerange",
        "backtest_cache",
        "export",
        "exportdirectory",
        "datadir",
    ):
        settings.pop(key, None)
    settings.update(
        dry_run=True,
        strategy=chosen,
        strategy_path=str(strategy_dir),
        user_data_dir=str(output),
        db_url="sqlite:///" + str(output / "paper.sqlite"),
        initial_state="running",
        bot_name="ta_crypto_paper_" + chosen.lower(),
        cancel_open_orders_on_exit=False,
        force_entry_enable=False,
    )
    save(output / "settings.json", settings)
    command = [
        str(Path(__file__).resolve().parents[2] / ".venv/bin/python"),
        "-m",
        "freqtrade",
        "trade",
        "--config",
        str(output / "settings.json"),
        "--strategy-path",
        str(strategy_dir),
    ]
    receipt = {
        "schema": "crypto-quant-paper-package.v1",
        "strategy": chosen,
        "purpose": "engineering_diagnostic" if diagnostic else "candidate_paper_validation",
        "historical_screen_passed": historical_pass,
        "bias_checks_passed": bool(bias_pass),
        "evaluation_sha256": sha(run / "evaluation.json"),
        "bias_sha256": sha(run / "bias/result.json"),
        "strategy_sha256": sha(strategy_source),
        "settings_sha256": sha(output / "settings.json"),
        "command": command,
        "dry_run": True,
        "real_orders_enabled": False,
        "deployment_authorized": False,
    }
    save(output / "package.json", receipt)
    return receipt


if __name__ == "__main__":
    import json

    parser = argparse.ArgumentParser(description="生成隔离的Freqtrade模拟运行包")
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--diagnostic-strategy", choices=CANDIDATES)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(
                args.run.resolve(),
                args.dataset.resolve(),
                args.output.resolve(),
                args.diagnostic_strategy,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
