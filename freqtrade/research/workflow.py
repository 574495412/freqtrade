"""冻结有限实验、训练期模型审查和分隔验收，不自动启用实盘。"""

import json
import os
import subprocess
import sys
from itertools import pairwise
from math import isfinite
from pathlib import Path

from .data import read, save, sha


CANDIDATES = ("CryptoTrend", "CryptoBreakout", "CryptoReversion")


def validate_plan(plan: dict) -> None:
    from datetime import date

    if plan["schema"] != "crypto-quant-research.v1" or tuple(plan["candidates"]) != CANDIDATES:
        raise ValueError("本版仅支持已登记的三个候选")
    if (plan["exchange"], plan["trading_mode"], plan["margin_mode"], plan["timeframe"]) != (
        "binance",
        "futures",
        "isolated",
        "1h",
    ):
        raise ValueError("本版研究市场/周期尚未适配")
    boundaries = [
        plan[key]
        for key in ("warmup_start", "train_start", "train_end", "validation_end", "holdout_end")
    ]
    if any(date.fromisoformat(a) >= date.fromisoformat(b) for a, b in pairwise(boundaries)):
        raise ValueError("训练和评价必须严格按时间分隔")
    if (
        plan["validation_start"] != plan["train_end"]
        or plan["holdout_start"] != plan["validation_end"]
    ):
        raise ValueError("评价边界不能重叠或偷偷改变")
    if (
        plan["leverage"] != 1
        or plan["initial_equity_usdt"] <= 0
        or plan["stake_usdt"] <= 0
        or plan["max_open_trades"] <= 0
        or plan["stake_usdt"] * plan["max_open_trades"] > plan["initial_equity_usdt"]
    ):
        raise ValueError("首版只支持无需借款的固定小额研究")
    if plan["deployment"]["live_orders_enabled"] is not False:
        raise ValueError("研究协议不能开启真实订单")
    costs = plan["extra_cost_bps_per_side"]
    if costs != [0, 3, 10] or not 0 < plan["fee_per_side"] < 0.01:
        raise ValueError("成本比较必须完整保留冻结三档")


def source_identity() -> dict:
    root = Path(__file__).resolve().parents[1]
    return {str(path.relative_to(root)): sha(path) for path in sorted(root.rglob("*.py"))}


def verify_freeze(run: Path, dataset: Path) -> dict:
    frozen = read(run / "freeze.json")
    if frozen["source_identity"] != source_identity():
        raise ValueError("源码改变；新实现必须使用新实验目录")
    if frozen["data_receipts_sha256"] != sha(dataset / "receipts.json"):
        raise ValueError("数据身份改变")
    plan = read(run / "plan.json")
    if frozen["plan_sha256"] != sha(run / "plan.json"):
        raise ValueError("事前协议改变")
    validate_plan(plan)
    return plan


def train(plan: dict, dataset: Path, run: Path) -> dict:
    from .engine import run_phase

    validate_plan(plan)
    run.mkdir(parents=True, exist_ok=False)
    save(run / "plan.json", plan)
    save(
        run / "freeze.json",
        {
            "schema": "crypto-quant-freeze.v1",
            "source_identity": source_identity(),
            "plan_sha256": sha(run / "plan.json"),
            "data_receipts_sha256": sha(dataset / "receipts.json"),
            "historical_evidence_status": plan["historical_evidence_status"],
            "orders_enabled": False,
        },
    )
    (run / "requirements.lock.txt").write_text(
        subprocess.run(
            [sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True, check=True
        ).stdout
    )
    return run_phase(plan, dataset, run / "train", "train")


def control_choice(training: dict, plan: dict) -> str:
    eligible = [
        row
        for row in training["summaries"]
        if row["extra_cost_bps_per_side"] == 3
        and row["net_return"] > 0
        and row["hourly_observed_drawdown"]
        <= plan["screening_targets"]["maximum_observed_drawdown"]
    ]
    return (
        max(eligible, key=lambda row: (row["net_return"], row["strategy"]))["strategy"]
        if eligible
        else "cash"
    )


def review(run: Path, dataset: Path, ta_root: Path, ta_python: Path) -> dict:
    plan = verify_freeze(run, dataset)
    if (run / "selection.json").exists() or (run / "agent_attempt.json").exists():
        raise ValueError("每个实验只允许一次模型审查；失败记录也不能自动覆盖")
    training = read(run / "train" / "result.json")
    evidence = {
        "phase": "train",
        "validation_results_included": False,
        "start": training["start"],
        "end": training["end"],
        "candidate_rules": {
            "CryptoTrend": "EMA24/96交叉及EMA200方向确认，反向交叉退出，8%止损",
            "CryptoBreakout": "72小时通道突破及EMA200方向，24小时反向边界退出，8%止损",
            "CryptoReversion": "20小时均值两倍标准差及RSI14 30/70，回均值退出，5%止损",
        },
        "summaries": [
            {key: value for key, value in row.items() if key != "by_pair"}
            for row in training["summaries"]
        ],
        "cost_model": plan["cost_model"],
        "screening_targets": plan["screening_targets"],
        "historical_evidence_status": plan["historical_evidence_status"],
    }
    save(run / "agent_input.json", evidence)
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "PATH",
            "HOME",
            "CODEX_HOME",
            "TMPDIR",
            "LANG",
            "LC_ALL",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "https_proxy",
            "http_proxy",
            "all_proxy",
            "no_proxy",
        }
    }
    env["PYTHONPATH"] = str(ta_root.resolve())
    save(
        run / "agent_attempt.json",
        {
            "status": "STARTED",
            "input_sha256": sha(run / "agent_input.json"),
            "tradingagents_root": str(ta_root),
            "python": str(ta_python),
        },
    )
    result = subprocess.run(
        [str(ta_python), str(Path(__file__).parent / "tradingagents_worker.py")],
        input=json.dumps(evidence, ensure_ascii=False),
        text=True,
        capture_output=True,
        env=env,
        cwd=run,
        timeout=570,
        check=False,
    )
    if result.returncode:
        (run / "agent_failure.log").write_text(result.stderr[-12000:])
        save(run / "agent_attempt.json", {"status": "FAILED", "returncode": result.returncode})
        raise RuntimeError("TradingAgents审查失败，原失败已保留；不伪造模型选择")
    response = json.loads(result.stdout)
    chosen = response["selection"]["candidate"]
    if (
        chosen not in (*CANDIDATES, "cash")
        or response["selection"]["production_eligible"] is not False
        or len(response["calls"]) != plan["agent_calls_per_review"]
    ):
        raise ValueError("模型选择或预算不符合冻结契约")
    save(run / "agent_response.json", response)
    selection = {
        "agent": chosen,
        "control": control_choice(training, plan),
        "input_sha256": sha(run / "agent_input.json"),
        "response_sha256": sha(run / "agent_response.json"),
        "validation_results_seen": False,
        "orders_enabled": False,
    }
    save(run / "selection.json", selection)
    save(run / "agent_attempt.json", {"status": "COMPLETED", "calls": len(response["calls"])})
    return selection


def screening(summaries: list[dict], candidate: str, plan: dict) -> dict:
    if candidate == "cash":
        return {"passed": False, "reasons": ["研究选择为空仓，尚无候选"]}
    rows = [row for row in summaries if row["strategy"] == candidate]
    targets = plan["screening_targets"]
    reasons = []
    if sorted(row["extra_cost_bps_per_side"] for row in rows) != plan["extra_cost_bps_per_side"]:
        reasons.append("成本情景缺失或重复")
    for row in rows:
        label = f"{row['extra_cost_bps_per_side']}bps"
        if not all(
            isfinite(row[key])
            for key in ("net_return", "profit_factor", "hourly_observed_drawdown", "closed_trades")
        ):
            reasons.append(label + "经济指标非有限数值")
            continue
        if row["closed_trades"] < targets["minimum_closed_trades_per_evaluation"]:
            reasons.append(label + "完整交易不足")
        if row["net_return"] <= 0:
            reasons.append(label + "净收益非正")
        if row["profit_factor"] < targets["minimum_profit_factor"]:
            reasons.append(label + "利润因子不足")
        if row["hourly_observed_drawdown"] > targets["maximum_observed_drawdown"]:
            reasons.append(label + "观察回撤超标")
        if row["capacity_review_required"]:
            reasons.append(label + "单笔容量需要复核")
    return {"passed": not reasons, "reasons": reasons}


def evaluate(run: Path, dataset: Path) -> dict:
    from .engine import run_phase

    plan = verify_freeze(run, dataset)
    selection = read(run / "selection.json")
    selection_sha = sha(run / "selection.json")
    reports = {}
    for phase in ("validation", "holdout", "continuous"):
        reports[phase] = run_phase(plan, dataset, run / phase, phase)
    if sha(run / "selection.json") != selection_sha:
        raise ValueError("评价期间训练期选择被修改")
    gates = {
        role: {
            phase: screening(report["summaries"], selection[role], plan)
            for phase, report in reports.items()
        }
        for role in ("agent", "control")
    }
    result = {
        "schema": "crypto-quant-evaluation.v1",
        "selection": selection,
        "screening": gates,
        "historical_economic_screen_passed": all(
            item["passed"] for item in gates["agent"].values()
        ),
        "agent_selection_differs_from_control": selection["agent"] != selection["control"],
        "economic_increment_demonstrated": False,
        "remaining": ["原生lookahead检查", "独立模拟运行与恢复核验", "账户及部署范围确定"],
        "production_eligible": False,
        "orders_enabled": False,
        "independent_oos": False,
    }
    save(run / "evaluation.json", result)
    return result
