"""Explain persisted strategy tags and display only correlated decision measurements."""

# ruff: noqa: RUF001 -- Chinese output.

import hashlib
import json
import re
from functools import cache

from deploy.live_prepare_20261109.notification_rule_catalog import CATALOG, STRATEGY_SOURCE


ENTRY_RULES = {
    "2": (
        "常规回撤做多",
        (
            "5 分钟收盘低于 EMA20 的 94.8%，StochRSI %K < 30，"
            "且 5 分钟和 15 分钟 Aroon Up 分别低于 30 和 50。"
        ),
    ),
    "101": (
        "快速回撤做多",
        (
            "5 分钟收盘低于 SMA16 的 94.6%，RSI14 < 36、RSI3 > 3、"
            "StochRSI %K < 20；同时通过 15 分钟 Aroon Up 和多周期保护条件。"
        ),
    ),
    "102": (
        "快速超卖做多",
        (
            "5 分钟 Williams %R < −95、StochRSI %K < 10，"
            "收盘同时低于布林下轨的 99.9% 和 EMA20 的 96%。"
        ),
    ),
    "120": (
        "分批交易模式做多",
        (
            "5 分钟收盘低于 EMA20 的 97.8%，StochRSI %K < 20、Williams %R < −80、"
            "Aroon Up < 25；同时要求币种与仓位槽符合该模式，多周期过滤通过。"
        ),
    ),
    "141": (
        "核心币种回撤做多",
        (
            "币种通过核心币种过滤；5 分钟 RSI20 较前一根下降，RSI3 < 30、"
            "Aroon Up < 25，且收盘低于 SMA16 的 96%。"
        ),
    ),
    "562": (
        "下轨破位做空",
        (
            "5 分钟收盘跌破布林下轨、EMA12 < EMA26、RSI3 < 20、RSI14 在 15 和 32 之间，"
            "CMF20 < −0.08；同时 15 分钟 RSI3 < 35 且 OBV 下降。"
        ),
    ),
}

NATIVE_REASONS = {
    "force_entry": "执行了显式强制开仓指令。",
    "force_exit": "执行了显式强制退出指令。",
    "stop_loss": "价格达到策略止损条件，触发退出。",
    "stoploss_on_exchange": "交易所保护止损单触发。",
    "trailing_stop_loss": "价格回撤触及移动止损位。",
    "liquidation": "引擎记录了清算退出。",
    "emergency_exit": "引擎触发紧急退出。",
    "roi": "达到当前持仓时间对应的策略收益阈值。",
    "exit_signal": "策略输出了退出信号。",
    "sold_on_exchange": "退出成交由交易所同步到账本。",
    "partial_exit": "策略仓位调整要求退出部分仓位。",
}

NFI_EXIT_RULES = {
    "gm0": (
        "首批仓位达到分批获利阈值，默认价差阈值为 1.8% 加双边费率。"
        "减仓数量还受最小交易额和剩余仓位约束。"
    ),
    "gmd0": "策略对首批仓位执行降风险退出。",
    "exit_short_rebuy_w_1_1": (
        "空单获利退出：策略收益比达到 1% 但低于 2%，StochRSI %K < 5，"
        "Williams %R(480) < −99，且 4 小时 Aroon Up < 5。"
    ),
    "exit_long_tc_o_3": (
        "核心币种获利退出：收盘在 EMA200 上方，策略收益比达到 3% 但低于 4%，且 RSI14 < 32。"
    ),
}

RAPID_EXIT_RULES = {
    "1": "RSI14 > 78",
    "2": "MFI14 > 84",
    "3": "Williams %R ≥ −0.1",
    "4": "RSI14 ≥ 72，且 5 分钟和 15 分钟 RSI3 均 > 90",
    "5": "15 分钟 RSI3 > 96",
    "6": "5 分钟和 15 分钟 RSI3 均 > 85",
    "7": "5 分钟 RSI3 > 90、15 分钟 RSI3 > 80",
    "8": "5 分钟 RSI3 > 92、15 分钟 RSI3 > 75",
    "9": "5 分钟 RSI3 > 94、15 分钟 RSI3 > 70",
    "10": "5 分钟 RSI3 > 99",
}

DECISION_LOG = re.compile(
    r" - [\w.]*NostalgiaForInfinityX8 - INFO - "
    r"(?:Grinding (?:entry|exit) \((?P<tag>[\w]+)\)|De-risk Level (?P<level>[1-4])) "
    r"(?:\[[^\]]+\] )?\[(?P<pair>[A-Z0-9]+/[A-Z0-9]+(?::[A-Z0-9]+)?)\]"
)
LOG_RATE = re.compile(r"\| Rate: (\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?=\s|$)")
LOG_PROFIT = re.compile(r"\| Profit: (-?\d+(?:\.\d+)?)%")
LOG_BATCH_PROFIT = re.compile(r"\| Grind profit: (-?\d+(?:\.\d+)?)%")
LOG_BRANCH = re.compile(r"\| Tag: (g\d{1,2})(?=\s|$)")
SAFE_TAG = re.compile(r"[a-zA-Z0-9_]+")


@cache
def verified_rules():
    try:
        data = json.loads(CATALOG.read_text())
        with STRATEGY_SOURCE.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        return data["entries"] if digest == data["source_sha256"] else {}
    except (OSError, ValueError, KeyError):
        return {}


def explain_entry(tag, rule):
    if tag in ENTRY_RULES:
        name, details = ENTRY_RULES[tag]
        return f"NFI X8 #{tag} {name}。规则：{details}"
    direction = "做多" if rule["side"] == "long" else "做空"
    # This source subset is only one mandatory gate, not the entire strategy.
    gate = rule["gate"].replace(" & ", " 且 ").replace(" | ", " 或 ")
    return f"NFI X8 #{tag} {direction}。源码条件之一：{gate}。"


def explain_adjustment(tag):
    derisk = re.fullmatch(r"derisk_level_([1-4])", tag)
    if derisk:
        return f"达到策略第 {derisk[1]} 档降风险条件，减掉部分仓位以降低暴露。"
    grind = re.fullmatch(r"grind_(\d+)_(entry|exit|derisk)", tag)
    if not grind:
        return None
    group, action = grind.groups()
    if action == "entry":
        return f"触发第 {group} 组分批补仓。该组有子单次数、价格间距、资金和信号检查。"
    if action == "exit":
        return f"第 {group} 组分批仓位达到获利退出条件，减少该批仓位。"
    return f"第 {group} 组分批仓位达到降风险条件，减少该批仓位。"


def explain_exit(tag):
    if plain := NFI_EXIT_RULES.get(tag) or explain_adjustment(tag):
        return plain
    rapid = re.fullmatch(r"exit_long_rapid_rpd_(\d+)", tag)
    if rapid and rapid[1] in RAPID_EXIT_RULES:
        return (
            "策略收益比高于 0.5% 且不超过 9%，"
            f"同时 {RAPID_EXIT_RULES[rapid[1]]}，触发快速获利退出。"
        )
    if re.fullmatch(r"exit_(long|short)_\w+_stoploss_doom", tag):
        return "触发该入场模式的强制风险止损分支。"
    if re.fullmatch(r"exit_profit_(long|short)_\w+_t_\d+_1(?:_m)?", tag):
        return "策略已记录过更高的获利目标，当前收益回落达到该模式的跟踪退出阈值。"
    if tag.startswith("exit_profit_"):
        return "触发策略获利退出子规则，具体阈值以该原始标签对应源码为准。"
    return None


def reason_lines(order_tag, evidence=None):
    tags = order_tag.strip().split() if order_tag else []
    if not tags or not SAFE_TAG.fullmatch(tags[0]):
        return ["原因：账本缺少可识别的策略标签。"]
    rules = verified_rules()
    tag = tags[0]
    if all(item.isdecimal() for item in tags) and rules:
        return [
            f"原因：{explain_entry(item, rules[item])}"
            if item in rules
            else f"原因：已记录信号 #{item}，条件未收录。"
            for item in dict.fromkeys(tags)
        ]
    reason = NATIVE_REASONS.get(tag) or (explain_exit(tag) if rules else None)
    lines = [f"原因：{reason or '该标签的详细条件未收录。'}（{tag}）"]
    facts = evidence or {}
    values = [
        pattern.format(facts[key])
        for key, pattern in (
            ("rate", "判断参考价 {}"),
            ("profit", "策略盈亏比 {}%"),
            ("batch_profit", "该批盈亏比 {}%"),
            ("branch", "补仓子信号 {}"),
        )
        if key in facts
    ]
    if values:
        lines.append(f"触发时日志：{'，'.join(values)}")
    return lines


def decision_from_log(line, stamp):
    match = DECISION_LOG.search(line)
    if match is None:
        return None
    tag = match["tag"] or f"derisk_level_{match['level']}"
    record = {"pair": match["pair"], "tag": tag, "stamp": stamp}
    for key, pattern in (
        ("rate", LOG_RATE),
        ("profit", LOG_PROFIT),
        ("batch_profit", LOG_BATCH_PROFIT),
        ("branch", LOG_BRANCH),
    ):
        value = pattern.search(line)
        if value:
            record[key] = value[1]
    return record


def order_decision(order, records):
    created = order["created_at"]
    tags = (order["order_tag"] or "").split()
    if created is None or not tags:
        return None
    # Strategy decision emission must precede the exchange's order clock and
    # match the pair and exact branch. Do not pick between ambiguous retries.
    matches = [
        row
        for row in records
        if row["pair"] == order["pair"]
        and row["tag"] == tags[0]
        and 0 <= created - row["stamp"] <= 20
    ]
    return matches[0] if len(matches) == 1 else None
