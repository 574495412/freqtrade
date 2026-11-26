"""Validate native source delegation and identical cached replay before economics."""

# ruff: noqa: S101
import json
import shutil
import socket
import sys
from pathlib import Path

from research.crypto.wavelet_policy_native_20261117_v3.source_policy import (
    ENGINE,
    FEATURE,
    bootstrap,
)
from research.crypto.wavelet_policy_native_20261117_v3.source_policy import (
    HERE as POLICY,
)


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def main():
    pd = bootstrap()["pd"]
    sys.path.insert(0, str(ENGINE))
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize.backtesting import Backtesting
    from freqtrade.persistence import LocalTrade
    from freqtrade.resolvers import ExchangeResolver

    output = ROOT / "user_data/crypto_quant" / HERE.name / "preflight"
    output.mkdir(parents=True, exist_ok=False)
    strategies = output / "strategies"
    strategies.mkdir()
    shutil.copyfile(HERE / "ResearchWaveletCPU.py", strategies / "ResearchWaveletCPU.py")
    template = json.loads(
        (ROOT / "user_data/crypto_quant" / POLICY.name / "attempt2/config.json").read_text()
    )
    cache = output / "features"
    results = []
    for capital, cost in [(1000, 20), (500, 13)]:
        run = output / (str(capital) + "_" + str(cost))
        run.mkdir()
        config = dict(template)
        config.update(
            strategy="ResearchWaveletCPU",
            strategy_path=str(strategies),
            user_data_dir=str(run),
            dry_run_wallet=capital,
            fee=cost / 10000,
            research_wavelet_cpu=True,
            research_feature_cache=str(cache),
            research_artifact_root=str(run),
        )
        path = run / "config.json"
        path.write_text(json.dumps(config, indent=2) + "\n")
        cfg = setup_optimize_configuration({"config": [str(path)]}, RunMode.BACKTEST)
        exchange = ExchangeResolver.load_exchange(cfg, validate=False, load_leverage_tiers=False)
        snapshot = json.loads(
            (
                FEATURE.with_name("spot_baseline_20261105") / "ccxt-spot-market-rules.json"
            ).read_text()
        )
        exchange._markets = snapshot["markets"]
        exchange._leverage_tiers = snapshot["leverage_tiers"]
        exchange._api.set_markets(snapshot["markets"], snapshot["currencies"])
        exchange._api_async.set_markets(snapshot["markets"], snapshot["currencies"])

        def no_network(*args, **kwargs):
            raise RuntimeError("Preflight attempted network")

        socket.socket.connect = no_network
        account = None
        try:
            account = Backtesting(cfg, exchange=exchange)
            strategy = account.strategylist[0]
            account._set_strategy(strategy)
            data, _ = account.load_bt_data()
            for pair, frame in data.items():
                calculated = strategy.advise_all_indicators({pair: frame})[pair]
                actual = strategy.ft_advise_signals(calculated, {"pair": pair})
                asset = pair.split("/")[0]
                reference = pd.read_parquet(
                    ROOT
                    / "user_data/crypto_quant"
                    / POLICY.name
                    / "attempt2"
                    / (asset + "_full2000.parquet")
                )
                pd.testing.assert_frame_equal(actual, reference)
            assert not LocalTrade.bt_trades and not LocalTrade.bt_trades_open
            trace = strategy.research_trace
            assert all(row["cache_hit"] == (capital == 500) for row in trace["feature_caches"])
            assert len(trace["model_events"]) > 0 if capital == 1000 else not trace["model_events"]
            results.append(
                {
                    "capital": capital,
                    "cost_bps": cost,
                    "all_source_columns_equal": True,
                    "trace": trace,
                    "financial_start_called": False,
                }
            )
        finally:
            if account is not None:
                account.cleanup()
            exchange.close()
    (HERE / "preflight.json").write_text(
        json.dumps(
            {
                "passed": True,
                "cases": results,
                "new_financial_wallets": 0,
                "cached_result_does_not_recompute_or_simulate_financial_results": True,
            },
            indent=2,
            default=str,
        )
        + "\n"
    )
    print("SOURCE_DELEGATION_AND_CACHE_PREFLIGHT_PASS", flush=True)


if __name__ == "__main__":
    main()
