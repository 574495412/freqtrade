"""Actual local Configuration->Backtesting.start->result ZIP, offline fixtures."""

import argparse
import json
import os
import socket
import traceback
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from prepare_fixture import HERE, OUT, ROOT, save, sha

from freqtrade.commands.optimize_commands import setup_optimize_configuration
from freqtrade.enums import RunMode
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.dated_price_precision import PriceRule, PriceRuleBook
from freqtrade.optimize.order_operations import OrderOperationMixin, OrderOperationWindow
from freqtrade.persistence import LocalTrade
from freqtrade.resolvers import ExchangeResolver


class NativeOperations(OrderOperationMixin, Backtesting):
    pass


def no_network(*args, **kwargs):
    raise RuntimeError("Frozen native fixture cannot access the network")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--attempt", default="audited")
    args = parser.parse_args()
    if any(k.startswith("FREQTRADE__") for k in os.environ):
        raise ValueError("Environment configuration overrides forbidden")
    plan = json.loads((HERE / "fixture_plan.json").read_text())
    case = next(c for c in plan["cases"] if c["name"] == args.case)
    amendments = json.loads((HERE / "serialization_amendment.json").read_text())
    for path, digest in {**case["inputs"], **plan["source_hashes"]}.items():
        amendment = amendments["source_changes"].get(path)
        if amendment:
            assert digest == amendment["before"] and sha(path) == amendment["after"]
        else:
            assert sha(path) == digest, path
    output = OUT / "runs" / args.case / args.attempt
    output.mkdir(parents=True, exist_ok=False)
    save(
        output / "producer_receipt.json",
        {
            "runner_sha256": sha(__file__),
            "save_helper_sha256": sha(HERE / "prepare_fixture.py"),
            "native_extension_sha256": sha(ROOT / "freqtrade/optimize/order_operations.py"),
            "plan_sha256": sha(HERE / "fixture_plan.json"),
            "amendment_sha256": sha(HERE / "serialization_amendment.json"),
        },
    )
    (output / "native_results").mkdir()
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
    native_completed = False
    try:
        account = NativeOperations(config, exchange=exchange)
        start, end = datetime.fromisoformat(plan["start"]), datetime.fromisoformat(plan["end"])
        at, until = (
            datetime.fromisoformat(plan["operation_start"]),
            datetime.fromisoformat(plan["operation_end"]),
        )
        digest = sha(HERE / "fixture_plan.json")
        rules = tuple(
            r
            for pair in plan["pairs"]
            for r in (
                PriceRule(pair, start, at, start - timedelta(days=1), Decimal("0.001"), digest),
                PriceRule(pair, at, end, start - timedelta(days=1), Decimal("0.01"), digest),
            )
        )
        book = PriceRuleBook(tuple(plan["pairs"]), rules)
        book.validate_window(start, end)
        windows = tuple(
            OrderOperationWindow(pair, at, until, start - timedelta(days=1), digest, case["policy"])
            for pair in plan["pairs"]
        )
        account.setup_order_operations(book, windows)
        account.start()
        native_completed = True
        stats = account.results["strategy"]["FixtureOperations"]
        save(output / "native_stats.json", stats)
        save(output / "operation_trace.json", account.operation_trace)
        result = {
            "case": args.case,
            "native_start_completed": True,
            "native_trades": stats["trades"],
            "open_native_trades": len(LocalTrade.bt_trades_open),
            "native_wallet_final": account.wallets.get_total("USDT"),
            "native_total_profit": LocalTrade.bt_total_profit,
            "native_zips": [
                {"path": str(p), "sha256": sha(p)}
                for p in (output / "native_results").glob("*.zip")
            ],
            "synthetic_only": True,
            "new_historical_financial_accounts": 0,
            "orders_enabled": False,
        }
        assert result["native_zips"] and not LocalTrade.bt_trades_open
        save(output / "result.json", result)
        print("NATIVE_START_COMPLETE", args.case, len(stats["trades"]), flush=True)
    except Exception as error:
        save(
            output / "failure.json",
            {
                "case": args.case,
                "error": str(error),
                "traceback": traceback.format_exc(),
                "native_start_completed": native_completed,
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
