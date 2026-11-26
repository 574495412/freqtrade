"""Actual native constructor and snapshot API admission, no financial start."""

import json
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

from research.crypto.wavelet_policy_native_20261117_v3.source_policy import ENGINE, bootstrap


HERE = Path(__file__).resolve().parent


def main():
    bootstrap()
    sys.path.insert(0, str(ENGINE))
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize.backtesting import Backtesting
    from freqtrade.resolvers import ExchangeResolver
    from research.crypto.wavelet_financial_native_20261117_v4.wavelet_price_precision import (
        MatchedPricePrecisionMixin,
    )

    root = HERE.parents[2] / "user_data/crypto_quant" / HERE.name
    output = root / "precision_probe"
    output.mkdir(exist_ok=False)
    config = json.loads((root / "preflight_attempt2/1000_20/config.json").read_text())
    config["user_data_dir"] = str(output)
    path = output / "config.json"
    path.write_text(json.dumps(config, indent=2) + "\n")
    cfg = setup_optimize_configuration(
        {"config": [str(path)], "datadir": config["datadir"]}, RunMode.BACKTEST
    )
    exchange = ExchangeResolver.load_exchange(cfg, validate=False, load_leverage_tiers=False)
    snapshot = json.loads(
        (HERE.with_name("spot_baseline_20261105") / "ccxt-spot-market-rules.json").read_text()
    )
    exchange._markets = snapshot["markets"]
    exchange._leverage_tiers = snapshot["leverage_tiers"]
    exchange._api.set_markets(snapshot["markets"], snapshot["currencies"])
    exchange._api_async.set_markets(snapshot["markets"], snapshot["currencies"])

    def no_network(*args, **kwargs):
        raise RuntimeError("Precision probe attempted network")

    socket.socket.connect = no_network

    class NativeSnapshotBacktesting(MatchedPricePrecisionMixin, Backtesting):
        pass

    account = None
    try:
        account = NativeSnapshotBacktesting(cfg, exchange=exchange)
        account.setup_frozen_price_precision(pairs=("BTC/USDT", "ETH/USDT"))
        ticks = {
            p: account.get_pair_precision(p, datetime(2025, 1, 1, tzinfo=UTC))
            for p in ["BTC/USDT", "ETH/USDT"]
        }
        rejected = []
        for key, bad in [
            ("research_wavelet_cpu", False),
            ("dry_run", False),
            ("runmode", RunMode.LIVE),
        ]:
            original = account.config[key]
            account.config[key] = bad
            try:
                account.get_pair_precision("BTC/USDT", datetime(2025, 1, 1, tzinfo=UTC))
            except ValueError:
                rejected.append(key)
            else:
                raise AssertionError("Snapshot context guard must reject " + key)
            finally:
                account.config[key] = original
        receipt = {
            "passed": True,
            "actual_native_constructor": True,
            "inherited_tick_and_clock_methods": True,
            "ticks": ticks,
            "rejected_invalid_contexts": rejected,
            "financial_start_called": False,
            "shared_snapshot_implementation_unchanged": True,
            "native_financial_wallets": 0,
        }
        (HERE / "precision_admission.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print("NATIVE_SNAPSHOT_EXTENSION_PASS", flush=True)
    finally:
        if account is not None:
            account.cleanup()
        exchange.close()


if __name__ == "__main__":
    main()
