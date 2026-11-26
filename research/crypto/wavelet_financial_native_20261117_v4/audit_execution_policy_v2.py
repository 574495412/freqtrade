"""Independent executed-fill versus frozen signal/liquidity/exit-clock checks.

Reads native result archives and already-generated source features. Never imports
the candidate calculator, model or engine and does not simulate another account.
"""

# ruff: noqa: S101
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pandas as pd


HERE = Path(__file__).resolve().parent
RISK_REASONS = {
    "stop_loss",
    "trailing_stop_loss",
    "force_exit",
    "emergency_exit",
    "stoploss_on_exchange",
    "liquidation",
    "stop",
}


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_rules(plan):
    config = read(plan["candidates"][0]["config"]["path"])
    cache = Path(config["research_feature_cache"])
    frames = {}
    for asset in ["BTC", "ETH"]:
        receipt = read(cache / (asset + "_USDT.json"))
        path = cache / (asset + "_USDT.parquet")
        assert sha(path) == receipt["output_sha256"]
        frame = pd.read_parquet(path).set_index("date")
        frame.index = frame.index.as_unit("ns")
        gain = frame.gain
        up = gain.clip(lower=0)
        down = gain.clip(upper=0)
        target = (up.rolling(40).mean() + 1.9 * up.rolling(40).std()).clip(lower=0.5).fillna(0)
        floor = (
            (down.rolling(40).mean() - 1.8 * abs(down.rolling(40).std())).clip(upper=-0.5).fillna(0)
        )
        frame["audit_enter"] = (
            (frame.predicted_gain > target)
            & (frame.volume > 0)
            & (frame.rvol > -1)
            & (frame.atr_pct_roll > -1)
            & (frame.guard_metric < 0)
            & (frame.close_norm < 1.1)
            & (frame.adx > -1)
            & (frame.bb_width > -1)
        )
        frame["audit_exit"] = (
            (frame.predicted_gain < floor) & (frame.rvol > 2) & (frame.guard_metric > 0)
        )
        frames[asset + "/USDT"] = frame
    return frames


def closed_row(frames, pair, timestamp):
    fill = pd.Timestamp(timestamp, unit="ms", tz="UTC")
    signal = fill.floor("15min") - pd.Timedelta(minutes=15)
    assert signal + pd.Timedelta(minutes=15) <= fill
    return frames[pair].loc[signal], fill, signal


def exit_contract(trade, frames, entry, exit_):
    reason = trade["exit_reason"]
    row, closed, _ = closed_row(frames, trade["pair"], exit_["order_filled_timestamp"])
    age = (
        closed - pd.Timestamp(trade["open_timestamp"], unit="ms", tz="UTC")
    ).total_seconds() / 3600
    if reason not in RISK_REASONS:
        assert age >= 1, (reason, age)
    net = (exit_["safe_price"] / entry["safe_price"]) * (1 - trade["fee_close"]) / (
        1 + trade["fee_open"]
    ) - 1
    if reason in {"exit_signal", "model_exit "}:
        assert bool(row.audit_exit)
    elif reason == "take_profit":
        assert net > 0.022 - 1e-9
    elif reason == "metric_overbought":
        assert net > 0 and row.guard_metric > 0.98
    elif reason == "unclog_12h":
        assert age >= 12 and net > 0.005 - 1e-9
    elif reason == "unclog_1d":
        assert age >= 24 and net >= -1e-9
    elif reason == "max_hold":
        assert age >= 21 * 24
    else:
        assert reason in RISK_REASONS | {"roi"}, reason
    return age


def main():
    output = HERE / "executed_policy_audit_v2.json"
    assert not output.exists(), "Preserve previous executed policy audit"
    plan = read(HERE / "plan_attempt2.json")
    frames = source_rules(plan)
    accounts = []
    for candidate in plan["candidates"]:
        directory = Path(plan["output_root"]) / (candidate["name"] + f"_{candidate['cost_bps']}bps")
        receipt = read(directory / "process_receipt.json")
        assert (
            receipt["returncode"] == 0
            and sha(directory / "result.json") == receipt["result_sha256"]
        )
        archives = list((directory / "native_results").glob("*.zip"))
        assert len(archives) == 1
        with ZipFile(archives[0]) as archive:
            name = next(
                n
                for n in archive.namelist()
                if n.endswith(".json") and not n.endswith("_config.json")
            )
            stats = json.loads(archive.read(name))["strategy"][candidate["strategy"]]
        log = (HERE / (candidate["name"] + "_attempt2_execution.log")).read_text()
        for marker in [
            "Strategy caused the following exception:",
            "Unexpected error ",
            "*** Exception in add_rolling_predictions()",
        ]:
            assert marker not in log, marker
        reasons = {}
        ratios = []
        for trade in stats["trades"]:
            assert not trade["is_short"] and trade["leverage"] == 1
            entries = [o for o in trade["orders"] if o["ft_is_entry"]]
            exits = [o for o in trade["orders"] if not o["ft_is_entry"]]
            assert len(entries) == len(exits) == 1
            row, _, _ = closed_row(frames, trade["pair"], entries[0]["order_filled_timestamp"])
            assert bool(row.audit_enter)
            quote = float(row.volume * row.close)
            used = entries[0]["amount"] * entries[0]["safe_price"]
            assert quote >= 1000 and used <= quote / 10 + 1e-7
            ratios.append(used / quote)
            exit_contract(trade, frames, entries[0], exits[0])
            reasons[trade["exit_reason"]] = reasons.get(trade["exit_reason"], 0) + 1
        accounts.append(
            {
                "account": candidate["name"],
                "native_trades": len(stats["trades"]),
                "entry_masks_and_closed_bar_maturity_passed": True,
                "liquidity_quote_headroom_passed": True,
                "maximum_trade_quote_to_prior_candle_quote_volume": max(ratios, default=0),
                "exit_clock_and_active_custom_exit_contract_passed": True,
                "source_callback_errors_in_log": 0,
                "exit_reasons": reasons,
                "native_archive_sha256": sha(archives[0]),
            }
        )
    output.write_text(
        json.dumps(
            {
                "passed": True,
                "accounts": accounts,
                "source_calculator_model_or_engine_imported": False,
                "protective_orders_or_live_execution_proven": False,
                "goal_complete": False,
            },
            indent=2,
        )
        + "\n"
    )
    print("EXECUTED_SOURCE_POLICY_AUDIT_PASS", flush=True)


if __name__ == "__main__":
    main()
