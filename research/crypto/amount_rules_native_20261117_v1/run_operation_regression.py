"""Replay two sealed operation fixtures on new native default amount hooks."""

import argparse
import inspect
import json
import socket
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

from prepare import HERE, OUT, ROOT, save, sha

from freqtrade.commands.optimize_commands import setup_optimize_configuration
from freqtrade.enums import RunMode
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.dated_price_precision import PriceRule, PriceRuleBook
from freqtrade.optimize.order_operations import OrderOperationMixin, OrderOperationWindow
from freqtrade.persistence import LocalTrade
from freqtrade.resolvers import ExchangeResolver


class OperationOnly(OrderOperationMixin, Backtesting):
    pass


def no_network(*a, **k):
    raise RuntimeError("Native regression remains offline")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("continue", "stop"), required=True)
    args = parser.parse_args()
    old = HERE.parent / "suspension_native_20261117_v1"
    prior = json.loads((old / "fixture_plan.json").read_text())
    case = next(c for c in prior["cases"] if c["name"] == args.case)
    current = json.loads((HERE / "fixture_plan.json").read_text())
    for path, digest in current["source_hashes"].items():
        assert sha(path) == digest
    for path, digest in case["inputs"].items():
        assert sha(path) == digest
    assert (
        Path(inspect.getfile(Backtesting)).resolve() == ROOT / "freqtrade/optimize/backtesting.py"
    )
    output = OUT / "operation_regression" / args.case
    output.mkdir(parents=True, exist_ok=False)
    (output / "native_results").mkdir()
    save(
        output / "producer.json",
        {
            "runner_sha256": sha(__file__),
            "new_native_sha256": sha(ROOT / "freqtrade/optimize/backtesting.py"),
            "old_plan_sha256": sha(old / "fixture_plan.json"),
            "old_inputs": case["inputs"],
        },
    )
    folder = Path(case["folder"])
    raw = json.loads((folder / "config.json").read_text())
    raw.update(
        datadir=str(folder / "candles"),
        user_data_dir=str(output),
        db_url="sqlite://",
        strategy_path=str(old),
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
    assert not config.get("research_dated_amount_rules", False)
    assert not any(config["exchange"].get(k) for k in ("key", "secret", "password"))
    socket.socket.connect = no_network
    exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
    metadata = json.loads((folder / "markets.json").read_text())
    exchange._markets, exchange._leverage_tiers = metadata["markets"], metadata["leverage_tiers"]
    exchange._api.set_markets(metadata["markets"], metadata["currencies"])
    exchange._api_async.set_markets(metadata["markets"], metadata["currencies"])
    exchange.validate_config(config)
    account = None
    try:
        account = OperationOnly(config, exchange=exchange)
        start, end, at, until = (
            datetime.fromisoformat(prior[k])
            for k in ("start", "end", "operation_start", "operation_end")
        )
        digest = sha(old / "fixture_plan.json")
        rules = tuple(
            r
            for pair in prior["pairs"]
            for r in (
                PriceRule(pair, start, at, start - timedelta(days=1), D("0.001"), digest),
                PriceRule(pair, at, end, start - timedelta(days=1), D("0.01"), digest),
            )
        )
        book = PriceRuleBook(tuple(prior["pairs"]), rules)
        windows = tuple(
            OrderOperationWindow(pair, at, until, start - timedelta(days=1), digest, case["policy"])
            for pair in prior["pairs"]
        )
        account.setup_order_operations(book, windows)
        account.start()
        stats = account.results["strategy"]["FixtureOperations"]
        expected_attempt = "audited_v2" if args.case == "continue" else "audited"
        expected = json.loads(
            (
                ROOT
                / "user_data/crypto_quant/suspension_native_20261117_v1/runs"
                / args.case
                / expected_attempt
                / "result.json"
            ).read_text()
        )
        projected = json.loads(json.dumps(stats["trades"], default=str))
        assert projected == expected["native_trades"]
        assert account.wallets.get_total("USDT") == expected["native_wallet_final"]
        save(
            output / "result.json",
            {
                "case": args.case,
                "native_start_completed": True,
                "trades_identical": True,
                "wallet_identical": True,
                "native_trades": projected,
                "wallet": account.wallets.get_total("USDT"),
                "open_trades": len(LocalTrade.bt_trades_open),
                "zips": [
                    {"path": str(p), "sha256": sha(p)}
                    for p in (output / "native_results").glob("*.zip")
                ],
                "synthetic_only": True,
            },
        )
        print("DEFAULT_OPERATION_REGRESSION", args.case, "identical", flush=True)
    finally:
        if account is not None:
            account.cleanup()
        exchange.close()


if __name__ == "__main__":
    main()
