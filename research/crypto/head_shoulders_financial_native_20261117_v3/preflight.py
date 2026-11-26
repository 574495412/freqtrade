"""One bounded native constructor/read-all40 probe; never financial start."""

import json
import resource
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

from .native_runner import checked, load_module, verify
from .PolicyReceipts import reject_callback_errors
from .release_guard import require_release


HERE = Path(__file__).parent.resolve()


def main():  # noqa: C901 - sequential bounded preflight gates
    plan_path = HERE / "plan.json"
    plan = json.loads(plan_path.read_text())
    candidate = plan["candidates"][0]
    engine = verify(plan, candidate)
    output = HERE / "native_preflight"
    output.mkdir(exist_ok=False)
    configs = [json.loads(checked(item["config"]).read_text()) for item in plan["candidates"]]
    for item, config in zip(plan["candidates"], configs, strict=True):
        if (
            config["dry_run_wallet"] != item["capital"]
            or config["fee"] != item["cost_bps"] / 10000
            or config["max_open_trades"] != 6
            or config["tradable_balance_ratio"] != 0.99
            or config["exchange"]["pair_whitelist"] != plan["pairs"]
        ):
            raise ValueError("Four-wallet static contract differs")
    if (HERE / "execution_release.json").exists():
        raise ValueError("Preparation preflight must run before root queue release")
    try:
        require_release(plan_path)
    except ValueError as error:
        release_block = str(error)
    else:
        raise AssertionError("Unreleased financial queue was admitted")
    synthetic = output / "expected_callback_error"
    synthetic.mkdir()
    try:
        reject_callback_errors(
            SimpleNamespace(
                callback_errors=[{"synthetic": True}], _pair_events={}, policy_audit=[]
            ),
            synthetic,
        )
    except ValueError:
        error_abort = True
    else:
        raise AssertionError("Callback error was not fatal")
    raw = configs[0]
    raw.update(
        user_data_dir=str(output),
        strategy_path=str(HERE / "sources"),
        timerange="1735686000-1735776000",
        export="none",
        db_url="sqlite://",
    )
    path = output / "config.json"
    path.write_text(json.dumps(raw, indent=2) + "\n")
    sys.path.insert(0, str(engine))
    import freqtrade
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize.backtesting import Backtesting
    from freqtrade.persistence import LocalTrade
    from freqtrade.resolvers import ExchangeResolver

    if Path(freqtrade.__file__).resolve() != engine / "freqtrade/__init__.py":
        raise ValueError("Wrong native engine")
    cfg = setup_optimize_configuration(
        {"config": [str(path)], "datadir": plan["datadir"]}, RunMode.BACKTEST
    )
    exchange = ExchangeResolver.load_exchange(cfg, validate=False, load_leverage_tiers=False)
    snapshot = json.loads(checked(plan["market_snapshot"]).read_text())
    exchange._markets = snapshot["markets"]
    exchange._leverage_tiers = snapshot["leverage_tiers"]
    exchange._api.set_markets(snapshot["markets"], snapshot["currencies"])
    exchange._api_async.set_markets(snapshot["markets"], snapshot["currencies"])
    exchange.validate_config(cfg)

    def no_network(*args, **kwargs):
        raise RuntimeError("Fixed native preflight attempted network")

    socket.socket.connect = no_network
    account = None
    try:
        precision = load_module(
            "preflight_legacy_precision", checked(plan["price_precision_source"])
        )

        class SharedLegacyBacktesting(precision.LegacyFiveMinutePrecisionMixin, Backtesting):
            pass

        account = SharedLegacyBacktesting(cfg, exchange=exchange)
        account.setup_legacy_five_minute_precision(plan)
        strategy = account.strategylist[0]
        account._set_strategy(strategy)
        data, _ = account.load_bt_data()
        if list(data) != plan["pairs"] or account.pairlists.whitelist != plan["pairs"]:
            raise ValueError("Original ordered40 pool differs")
        records = []
        import pandas as pd

        grid = pd.date_range(plan["start"], plan["comparison_end"], freq="h")
        precision_checks = 0
        oracle = SimpleNamespace(
            price_pair_prec=account._legacy5m_precision,
            exchange=exchange,
            precision_mode_price=account.precision_mode_price,
        )
        for pair, frame in data.items():
            calculated = strategy.advise_all_indicators({pair: frame})[pair]
            calculated = strategy.ft_advise_signals(calculated, {"pair": pair})
            if len(frame) != 25 or len(account.detail_data[pair]) != 300:
                raise ValueError("Bounded native probe calendar differs")
            records.append(
                {
                    "pair": pair,
                    "native_hour_rows": len(frame),
                    "native_detail_rows": len(account.detail_data[pair]),
                    "entry_masks": int(calculated.enter_long.sum() + calculated.enter_short.sum()),
                }
            )
            for clock in grid:
                if account.get_pair_precision(pair, clock) != Backtesting.get_pair_precision(
                    oracle, pair, clock
                ):
                    raise ValueError("Shared reference diverges from exact original native asof")
                precision_checks += 1
            print("PREFLIGHT_PAIR_AND_LEGACY_PRECISION_PASS", pair, flush=True)
        reject_callback_errors(strategy, output)
        if (
            LocalTrade.bt_trades
            or LocalTrade.bt_trades_open
            or account.trade_id_counter
            or account.order_id_counter
        ):
            raise ValueError("Preflight unexpectedly created native financial state")
        verify(plan, candidate)
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss = peak if sys.platform == "darwin" else peak * 1024
        if rss > plan["memory_guard_bytes"]:
            raise ValueError("Native preflight exceeds registered RSS guard")
        receipt = {
            "passed": True,
            "native_constructor_and_exchange_validation": True,
            "all40_native_provider_and_copied_policy_callbacks": True,
            "records": records,
            "four_capital_fee_configs_checked": len(configs),
            "actual_supported_precision_calls_equal_native_legacy_asof": precision_checks,
            "unreleased_queue_rejection": release_block,
            "callback_errors_abort": error_abort,
            "peak_rss_bytes": rss,
            "full40_financial_resource_feasibility_proven": False,
            "full_window_v2_interface_receipt": plan["policy_interface_receipt"],
            "financial_start_called": False,
            "native_financial_accounts": 0,
            "orders_enabled": False,
            "goal_complete": False,
        }
        (HERE / "preflight.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print("FINANCIAL_PREPARATION_PREFLIGHT_PASS", flush=True)
    finally:
        if account is not None:
            account.cleanup()
        exchange.close()


if __name__ == "__main__":
    main()
