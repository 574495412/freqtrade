"""Offline Configuration->local extended native Backtesting.start->actual ZIP."""

import argparse
import inspect
import json
import os
import socket
import traceback
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

from research.crypto.joint_rules_native_20261117_v1.prepare import HERE, OUT, ROOT, save, sha

from freqtrade.commands.optimize_commands import setup_optimize_configuration
from freqtrade.enums import RunMode
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.dated_amount_rules import AmountRule, AmountRuleBook, DatedAmountMixin
from freqtrade.optimize.dated_price_precision import (
    PriceRule,
    PriceRuleBook,
)
from freqtrade.optimize.order_operations import OrderOperationMixin, OrderOperationWindow
from freqtrade.persistence import LocalTrade
from freqtrade.resolvers import ExchangeResolver


class NativeJoint(DatedAmountMixin, OrderOperationMixin, Backtesting):
    pass


def no_network(*args, **kwargs):
    raise RuntimeError("Joint native fixture cannot access network")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    args = parser.parse_args()
    assert (
        Path(inspect.getfile(Backtesting)).resolve() == ROOT / "freqtrade/optimize/backtesting.py"
    )
    if any(k.startswith("FREQTRADE__") for k in os.environ):
        raise ValueError("Environment overrides forbidden")
    plan = json.loads((HERE / "fixture_plan.json").read_text())
    case = next(c for c in plan["cases"] if c["name"] == args.case)
    for path, digest in {**plan["source_hashes"], **case["inputs"]}.items():
        assert sha(path) == digest, path
    output = OUT / "runs" / args.case
    output.mkdir(parents=True, exist_ok=False)
    (output / "native_results").mkdir()
    save(
        output / "producer_receipt.json",
        {"source_hashes": plan["source_hashes"], "plan_sha256": sha(HERE / "fixture_plan.json")},
    )
    folder = Path(case["folder"])
    raw = json.loads((folder / "config.json").read_text())
    raw.update(
        datadir=str(folder / "candles"),
        user_data_dir=str(output),
        db_url="sqlite://",
        exportdirectory=str(output / "native_results"),
    )
    save(output / "config.json", raw)
    config = setup_optimize_configuration(
        {
            "config": [str(output / "config.json")],
            "datadir": raw["datadir"],
            "exportdirectory": output / "native_results",
        },
        RunMode.BACKTEST,
    )
    assert config["dry_run"] and config["runmode"] == RunMode.BACKTEST
    assert not any(config["exchange"].get(k) for k in ("key", "secret", "password"))
    socket.socket.connect = no_network
    exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
    metadata = json.loads((folder / "markets.json").read_text())
    exchange._markets, exchange._leverage_tiers = metadata["markets"], metadata["leverage_tiers"]
    exchange._api.set_markets(metadata["markets"], metadata["currencies"])
    exchange._api_async.set_markets(metadata["markets"], metadata["currencies"])
    exchange.validate_config(config)
    account = None
    attempted = completed = False
    try:
        account = NativeJoint(config, exchange=exchange)
        pairs = tuple(plan["pairs"])
        start, end, changed, at, until = (
            datetime.fromisoformat(plan[k])
            for k in ("start", "end", "quantity_transition", "operation_start", "operation_end")
        )
        digest = sha(HERE / "fixture_plan.json")
        price = PriceRuleBook(
            pairs,
            tuple(
                rule
                for pair in pairs
                for rule in (
                    PriceRule(pair, start, at, start - timedelta(days=1), D("0.001"), digest),
                    PriceRule(pair, at, end, start - timedelta(days=1), D("0.01"), digest),
                )
            ),
        )
        windows = tuple(
            OrderOperationWindow(pair, at, until, start - timedelta(days=1), digest, case["policy"])
            for pair in pairs
        )
        account.setup_order_operations(price, windows)
        records = []
        for pair in pairs:
            for lo, hi, step in ((start, changed, "1"), (changed, at, "0.1"), (at, end, "0.1")):
                if lo == at and args.case == "unknown_amount":
                    continue
                if lo == at and args.case == "coarse_dust" and pair == pairs[0]:
                    step = "1"
                records.append(
                    AmountRule(
                        pair,
                        lo,
                        hi,
                        start - timedelta(days=1),
                        D(step),
                        D(step),
                        D(1000),
                        D(50),
                        digest,
                        True,
                    )
                )
        amount = AmountRuleBook(pairs, tuple(records))
        amount.validate_window(start, end)
        price.validate_window(start, end)
        account.setup_dated_amount_rules(amount)
        save(
            output / "joint_policy.json",
            {
                "mro": [c.__name__ for c in type(account).__mro__],
                "price_rulebook": str(price),
                "amount_rulebook": str(amount),
                "operation_windows": str(windows),
                "source_hashes": plan["source_hashes"],
                "plan_sha256": digest,
            },
        )
        attempted = True
        account.start()
        completed = True
        stats = account.results["strategy"]["FixtureJoint"]
        save(output / "native_stats.json", stats)
        save(output / "amount_trace.json", account.amount_rule_trace)
        save(output / "operation_trace.json", account.operation_trace)
        result = {
            "case": args.case,
            "native_start_attempted": True,
            "native_start_completed": True,
            "native_wallet_final": account.wallets.get_total("USDT"),
            "native_total_profit": LocalTrade.bt_total_profit,
            "open_trades": len(LocalTrade.bt_trades_open),
            "trades": stats["trades"],
            "zips": [
                {"path": str(p), "sha256": sha(p)}
                for p in (output / "native_results").glob("*.zip")
            ],
            "synthetic_only": True,
            "new_historical_strategy_accounts": 0,
            "orders_enabled": False,
        }
        assert result["zips"] and not LocalTrade.bt_trades_open
        save(output / "result.json", result)
        print("NATIVE_COMPLETE", args.case, len(stats["trades"]), flush=True)
    except Exception as error:
        if account is not None:
            save(output / "amount_trace.json", getattr(account, "amount_rule_trace", []))
            save(output / "operation_trace.json", getattr(account, "operation_trace", []))
        save(
            output / "failure.json",
            {
                "case": args.case,
                "error": str(error),
                "traceback": traceback.format_exc(),
                "native_start_attempted": attempted,
                "native_start_completed": completed,
                "open_trades": [t.to_json() for t in LocalTrade.bt_trades_open],
                "synthetic_only": True,
            },
        )
        raise
    finally:
        if account is not None:
            account.cleanup()
        exchange.close()


if __name__ == "__main__":
    main()
