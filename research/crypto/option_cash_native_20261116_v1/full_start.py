"""Full inherited synthetic start/load/signals/matching/wallet/export lifecycle."""

import argparse
import socket
from datetime import timedelta

import pandas as pd
from native_probe import END, HERE, START, PremiumProbe, build, marks, order_snapshot, save

from freqtrade.data.history.inverse_options import OptionChartContract, OptionChartFeatherHandler
from freqtrade.enums import CandleType
from freqtrade.optimize.option_fixture_lifecycle import OptionFixtureLifecycleMixin
from freqtrade.persistence import LocalTrade, Trade


class FullPremiumProbe(OptionFixtureLifecycleMixin, PremiumProbe):
    pass


def main(currency, attempt):
    old = Trade.use_db
    Trade.use_db = False
    LocalTrade.reset_trades()
    account = exchange = None
    folder = HERE / "runs" / attempt
    try:
        account, exchange, _ = build(
            currency, "short", folder, scheduled=True, account_type=FullPremiumProbe
        )
        hours = tuple(pd.date_range(START - timedelta(hours=1), END, freq="h"))
        account.option_chart_contracts, account.option_mark_inputs = {}, {}
        for spec in account.wallets.contract.specs:
            contract = OptionChartContract(spec, hours)
            price = [0.05] * (len(hours) - 1) + [0.04]
            frame = pd.DataFrame(
                {
                    "date": hours,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 100.0,
                }
            )
            OptionChartFeatherHandler(folder, contract).ohlcv_store(
                spec.storage_pair, "1h", frame, CandleType.SPOT
            )
            frame.to_csv(folder / (spec.instrument_name + ".csv"), index=False)
            account.option_chart_contracts[spec.storage_pair] = contract
        for at in hours[1:]:
            account.option_mark_inputs[at.to_pydatetime()] = marks(
                account.wallets.contract, at, ".04" if at == END else ".05"
            )
        account.start()
        zips = list((folder / "backtest_results").glob("*.zip"))
        assert len(zips) == 1
        assert not account.wallets.cash_state.positions
        assert len(account.option_boundaries) == 16
        result = {
            "currency": currency,
            "case": "full_start",
            "synthetic_only": True,
            "native_constructor": True,
            "native_backtest_start": True,
            "native_history_files": 2,
            "actual_zip": str(zips[0]),
            "snapshots": account.option_boundaries,
            "orders": order_snapshot(),
            "actual_fill_count": len(account.wallets.fill_journal),
            "final_cash_state": account.option_boundaries[-1]["state"],
            "native_zip_statistics": "Gross diagnostic;not fee-overlay strategy returns",
            "historical_option_accounts": 0,
            "factor_analysis": 0,
            "bench": 0,
            "strategy_net_return": None,
            "option_liquidation_or_expiry_certified": False,
        }
        save(folder / "native_trace.json", result)
        print(
            currency,
            "FULL SYNTHETIC START",
            len(account.wallets.fill_journal),
            "fills",
            len(zips),
            "ZIP",
        )
    finally:
        if account is not None:
            account.cleanup()
        if exchange is not None:
            exchange.close()
        LocalTrade.reset_trades()
        Trade.use_db = old


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--currency", choices=["BTC", "ETH"], required=True)
    args.add_argument("--attempt", required=True)
    parsed = args.parse_args()

    def offline(*args, **kwargs):
        raise RuntimeError("Synthetic option lifecycle is offline")

    socket.socket.connect = offline
    main(parsed.currency, parsed.attempt)
