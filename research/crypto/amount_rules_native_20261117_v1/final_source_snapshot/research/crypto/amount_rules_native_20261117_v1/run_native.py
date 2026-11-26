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

from prepare import HERE, OUT, ROOT, save, sha

from freqtrade.commands.optimize_commands import setup_optimize_configuration
from freqtrade.enums import RunMode
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.dated_amount_rules import AmountRule, AmountRuleBook, DatedAmountMixin
from freqtrade.optimize.dated_price_precision import (
    DatedPricePrecisionMixin,
    PriceRule,
    PriceRuleBook,
)
from freqtrade.persistence import LocalTrade
from freqtrade.resolvers import ExchangeResolver


class NativeAmount(DatedAmountMixin, DatedPricePrecisionMixin, Backtesting):
    pass


def no_network(*args, **kwargs):
    raise RuntimeError("Native amount fixture cannot access network")


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
        account = NativeAmount(config, exchange=exchange)
        pair = plan["pair"]
        start, end, at = (datetime.fromisoformat(plan[k]) for k in ("start", "end", "transition"))
        digest = sha(HERE / "fixture_plan.json")
        price = PriceRuleBook(
            (pair,), (PriceRule(pair, start, end, start - timedelta(days=1), D("0.01"), digest),)
        )
        account.setup_dated_price_rules(price)
        records = []
        for bounds, spec in zip(
            ((start, at), (at, end)), plan["rule_specs"][args.case], strict=True
        ):
            if spec is not None:
                step, minimum, notional = map(D, spec)
                records.append(
                    AmountRule(
                        pair,
                        *bounds,
                        start - timedelta(days=1),
                        step,
                        minimum,
                        D(1000),
                        notional,
                        digest,
                        True,
                    )
                )
        amount = AmountRuleBook((pair,), tuple(records))
        amount.validate_window(start, end)
        price.validate_window(start, end)
        account.setup_dated_amount_rules(amount)
        attempted = True
        account.start()
        completed = True
        stats = account.results["strategy"]["FixtureAmount"]
        save(output / "native_stats.json", stats)
        save(output / "amount_trace.json", account.amount_rule_trace)
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
