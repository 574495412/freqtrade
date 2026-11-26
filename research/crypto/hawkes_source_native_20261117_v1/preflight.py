"""Actual fixed native constructor, data provider and strategy signal checks."""

import importlib.util
import json
import shutil
import socket
import sys
from pathlib import Path

from research.crypto.hawkes_source_native_20261117_v1.native_runner import checked, verify

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    plan = json.loads((HERE / "plan.json").read_text())
    sys.path.insert(0, str(verify(plan, plan["candidates"][0])))
    import numpy as np
    import pandas as pd
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.persistence import LocalTrade
    from freqtrade.resolvers import ExchangeResolver
    from research.crypto.hawkes_source_native_20261117_v1.HawkesInputs import build_features

    output = Path(plan["output_root"]) / "preflight"
    output.mkdir(exist_ok=False)
    strategies = output / "strategies"
    strategies.mkdir()
    for name, source in plan["candidates"][0]["source_files"].items():
        shutil.copyfile(checked(source), strategies / name)
    precision = load("hawkes_precision_probe", checked(plan["price_precision_source"]))
    funding = load("hawkes_funding_probe", checked(plan["funding_extension"]))

    class NativeProbe(precision.MatchedPricePrecisionMixin, funding.FundingOwnershipBacktesting):
        pass

    results = []
    for candidate in [plan["candidates"][0], plan["candidates"][4]]:
        run = output / candidate["funding_ownership"]
        run.mkdir()
        config = json.loads(checked(candidate["config"]).read_text())
        config.update(datadir=plan["datadir"], user_data_dir=str(run),
                      strategy_path=str(strategies), db_url="sqlite://",
                      timerange="1735686000-1735772400")
        file = run / "config.json"
        file.write_text(json.dumps(config, indent=2) + "\n")
        cfg = setup_optimize_configuration({"config": [str(file)], "datadir": plan["datadir"]}, RunMode.BACKTEST)
        exchange = ExchangeResolver.load_exchange(cfg, validate=False, load_leverage_tiers=False)
        markets = json.loads(checked(candidate["market_snapshot"]).read_text())
        exchange._markets = markets["markets"]
        exchange._leverage_tiers = markets["leverage_tiers"]
        exchange._api.set_markets(markets["markets"], markets["currencies"])
        exchange._api_async.set_markets(markets["markets"], markets["currencies"])

        def no_network(*args, **kwargs):
            raise RuntimeError("Frozen preflight attempted network")

        socket.socket.connect = no_network
        account = None
        try:
            account = NativeProbe(cfg, exchange=exchange, funding_ownership=candidate["funding_ownership"])
            account.setup_frozen_price_precision(pairs=tuple(candidate["pairs"]))
            strategy = account.strategylist[0]
            account._set_strategy(strategy)
            data, _ = account.load_bt_data()
            cases = []
            for pair, frame in data.items():
                assert frame.date.iloc[0] == pd.Timestamp("2023-08-01T00:00:00Z")
                actual = strategy.advise_all_indicators({pair: frame})[pair]
                actual = strategy.ft_advise_signals(actual, {"pair": pair})
                expected = build_features(frame)
                for name in expected:
                    np.testing.assert_array_equal(actual["hawkes_" + name], expected[name])
                for label, wanted in [("enter_long", expected.signal.eq(1)),
                                      ("enter_short", expected.signal.eq(-1)),
                                      ("exit_long", expected.signal.ne(1)),
                                      ("exit_short", expected.signal.ne(-1))]:
                    np.testing.assert_array_equal(actual[label], wanted.astype(int))
                cases.append({"pair": pair, "rows": len(frame), "source_features_and_native_signals_exact": True})
            assert not LocalTrade.bt_trades and not LocalTrade.bt_trades_open
            cfg["research_hawkes"] = False
            try:
                account._require_matched_context()
            except ValueError:
                rejected = True
            else:
                raise AssertionError("Unregistered context accepted")
            cfg["research_hawkes"] = True
            results.append({"funding_policy": candidate["funding_ownership"], "cases": cases,
                            "missing_flag_rejected": rejected, "financial_start_called": False})
        finally:
            if account is not None:
                account.cleanup()
            exchange.close()
    (HERE / "preflight.json").write_text(json.dumps({"passed": True, "cases": results,
        "new_financial_accounts": 0}, indent=2) + "\n")
    print("NATIVE_PREFLIGHT_PASS")


if __name__ == "__main__":
    main()
