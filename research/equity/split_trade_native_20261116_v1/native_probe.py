"""Real native initialization and backtest loop on explicit synthetic inputs.

Synthetic starts only. Historical stock entry remains closed. Native default
trade statistics remain distinct from this net-boundary result.
"""

import argparse
import hashlib
import json
import shutil
import socket
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
from bootstrap import HERE, PARENT, ROOT, boot


boot()
from test_sessions import routed

from freqtrade.commands.optimize_commands import setup_optimize_configuration
from freqtrade.data.equity_dividends import CashDividend
from freqtrade.data.equity_provider import EquityDataProvider
from freqtrade.data.equity_splits import ImplementedShareSplit, ShareSplitContract
from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
from freqtrade.enums import RunMode
from freqtrade.equity_dividend_wallets import EquityDividendWallets
from freqtrade.equity_split_wallets import EquitySplitWallets
from freqtrade.equity_wallets import SettlementDay
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.equity_fee_execution import EquityFeeFillMixin
from freqtrade.optimize.equity_fixture_lifecycle import EquityFixtureLifecycleMixin
from freqtrade.optimize.equity_net_result import EquityFixtureNetResultMixin
from freqtrade.optimize.equity_open_only import EquityOpenOnlyMixin
from freqtrade.optimize.equity_sessions import EquitySessionRoutingMixin
from freqtrade.optimize.equity_split_execution import EquitySplitTradeMixin
from freqtrade.persistence import LocalTrade, Trade
from freqtrade.resolvers import ExchangeResolver


class DefaultProbe(
    EquityFixtureNetResultMixin,
    EquityFixtureLifecycleMixin,
    EquityOpenOnlyMixin,
    EquitySessionRoutingMixin,
    EquityFeeFillMixin,
    Backtesting,
):
    pass


class OpenProbe(EquitySplitTradeMixin, DefaultProbe):
    pass


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, default=str, allow_nan=False) + "\n")


def run(market, output, *, split_case, future_extrema=False, terminal_held=True):
    old = Trade.use_db
    Trade.use_db = False
    LocalTrade.reset_trades()
    reference, pair, source = routed(market)
    if future_extrema:
        source["high"], source["low"] = source.open * 100, source.open * 0.001
    if terminal_held:
        source.loc[source.index[-3:], "close"] = 101.0
    plan, original_wallet = reference.equity_session_plan, reference.wallets
    # A settlement tail is not a longer trading/evaluation window. Keep the
    # originally missing-tail failure; add synthetic cash release dates only.
    cash = replace(
        original_wallet.contract,
        settlement_days=original_wallet.contract.settlement_days
        + tuple(
            SettlementDay(
                date(2025, 1, d), datetime(2025, 1, d, 22 if market == "XNYS" else 10, tzinfo=UTC)
            )
            for d in (13, 14)
        ),
    )
    fee_contract = replace(original_wallet.fees, cash=cash)
    fixture_sha = hashlib.sha256((HERE / "fixture_definitions.json").read_bytes()).hexdigest()
    announced = datetime(2025, 1, 1, tzinfo=UTC)
    terms = {
        "default": [],
        "none": [],
        "forward_exit": [(1, 2, 1)],
        "reverse_exit": [(1, 1, 2)],
        "forward_held": [(5, 2, 1)],
        "thirds_held": [(5, 3, 1)],
        "same_open_buy": [(0, 2, 1)],
        "forward_reverse": [(1, 2, 1), (5, 1, 2)],
        "future2": [(6, 2, 1)],
        "future4": [(6, 4, 1)],
        "fractional_reject": [(1, 1, 4)],
    }[split_case]
    split_events = tuple(
        ImplementedShareSplit(
            "synthetic-implemented-" + str(i),
            pair,
            plan.opens[index],
            announced,
            new,
            old,
            fixture_sha,
        )
        for i, (index, new, old) in enumerate(terms)
    )
    splits = ShareSplitContract(
        cash, (pair,), plan.opens[0], plan.closes[-1], split_events, fixture_sha
    )
    # Explicit synthetic post-event nominal quotes. This fixture preparation is
    # not a production adjustment of historical securities or actual prices.
    for event in split_events:
        mask = source.date >= pd.Timestamp(event.effective_open).normalize()
        source.loc[mask, ["open", "high", "low", "close"]] = (
            source.loc[mask, ["open", "high", "low", "close"]] * event.old_shares / event.new_shares
        ).round(2)
    events = []
    if split_case not in {"default", "none"}:
        events.append(
            CashDividend(
                "post-split-share-dividend",
                pair,
                plan.opens[6],
                announced,
                Decimal(1),
                Decimal(".1"),
                announced,
                fixture_sha,
                fixture_sha,
                plan.closes[6],
                plan.closes[5],
                fixture_sha,
            )
        )
    dividend_contract = replace(
        original_wallet.dividends, cash=cash, events=tuple(events), source_sha256=fixture_sha
    )
    raw = json.loads(
        (
            ROOT
            / "user_data/crypto_quant/copula_fills_native_20261115_v4"
            / "fixtures/normal1000/config.json"
        ).read_text()
    )
    raw.update(
        strategy="OpenClockProbe",
        strategy_path=str(PARENT),
        timeframe="1d",
        timeframe_detail="",
        dry_run_wallet=1000,
        stake_amount=600,
        stake_currency=plan.currency,
        max_open_trades=1,
        tradable_balance_ratio=1.0,
        fee=0.0,
        trading_mode="spot",
        margin_mode="",
        position_adjustment_enable=False,
        trailing_stop=False,
        minimal_roi={},
        stoploss=-0.99,
        synthetic_equity_fixture=True,
        equity_execution_model="completed_close_next_open_v1",
        user_data_dir=str(output),
        datadir=str(output),
        db_url="sqlite://",
    )
    raw["timerange"] = "20250102-20250111"
    raw["backtest_cache"] = "none"
    raw["export"] = "trades"

    def config_candle():
        from freqtrade.enums import CandleType

        return CandleType.SPOT

    raw["exchange"].update(pair_whitelist=[pair], pair_blacklist=[], key="", secret="", password="")
    raw["pairlists"] = [{"method": "StaticPairList"}]
    output.mkdir(parents=True, exist_ok=False)
    save(output / "config.json", raw)
    source.to_feather(output / "synthetic_ohlcv.feather")
    FeatherDataHandler(output).ohlcv_store(pair, "1d", source, candle_type=config_candle())
    bound = {}
    for p in (
        list(HERE.glob("*.py"))
        + list((ROOT / "freqtrade/optimize").glob("equity*.py"))
        + [HERE / "protocol.json", output / "synthetic_ohlcv.feather", output / "config.json"]
    ):
        bound[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    for path, digest in json.loads((HERE / "implementation_freeze.json").read_text()).items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
        bound[path] = digest
    save(output / "implementation_freeze.json", bound)
    snapshot_manifest = {}
    for path, digest in bound.items():
        p = Path(path)
        if p.is_relative_to(output):
            continue
        target = output / "source_snapshot" / p.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)
        snapshot_manifest[path] = {"sha256": digest, "snapshot": str(target)}
    save(output / "source_snapshot_manifest.json", snapshot_manifest)
    config = setup_optimize_configuration(
        {"config": [str(output / "config.json")], "datadir": str(output)}, RunMode.BACKTEST
    )
    exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
    template = json.loads(
        (
            ROOT
            / "user_data/crypto_quant/copula_fills_native_20261115_v4"
            / "fixtures/normal1000/synthetic_markets_maintenance_fixed.json"
        ).read_text()
    )
    meta = deepcopy(next(iter(template["markets"].values())))
    meta.update(
        id="ABC" + plan.currency,
        symbol=pair,
        base="ABC",
        quote=plan.currency,
        baseId="ABC",
        quoteId=plan.currency,
        settle=None,
        settleId=None,
        type="spot",
        spot=True,
        swap=False,
        future=False,
        contract=False,
        linear=None,
        inverse=None,
        contractSize=None,
        active=True,
    )
    meta["precision"] = {"amount": 1.0, "price": 0.01}
    meta["limits"]["amount"] = {"min": 1.0, "max": 1e9}
    meta["limits"]["cost"] = {"min": 1.0, "max": 1e12}
    currencies = {c: {"id": c, "code": c, "precision": 8} for c in ["ABC", plan.currency]}
    exchange._markets = {pair: meta}
    exchange._api.set_markets({pair: meta}, currencies)
    exchange._api_async.set_markets({pair: meta}, currencies)
    exchange.validate_config(config)
    save(output / "synthetic_market.json", {pair: meta})
    save(
        output / "synthetic_contracts.json",
        {
            "cash": asdict(cash),
            "fees": asdict(fee_contract),
            "dividends": asdict(dividend_contract),
            "splits": asdict(splits),
            "execution": asdict(replace(reference.equity_execution_contract, cash=cash)),
        },
    )
    account = None
    try:
        account = (DefaultProbe if split_case == "default" else OpenProbe)(
            config, exchange=exchange
        )
        account.equity_session_plan = plan
        account.equity_execution_contract = replace(reference.equity_execution_contract, cash=cash)
        account.dataprovider = EquityDataProvider(config, reference.dataprovider.schedules)
        if split_case == "default":
            account.wallets = EquityDividendWallets(
                config, exchange, fees=fee_contract, dividends=dividend_contract, asof=plan.opens[0]
            )
        else:
            account.wallets = EquitySplitWallets(
                config,
                exchange,
                fees=fee_contract,
                dividends=dividend_contract,
                splits=splits,
                asof=plan.opens[0],
            )
        callbacks = []

        def observe(current_time, **kwargs):
            visible, known = account.dataprovider.get_analyzed_dataframe(pair, "1d")
            assert known < current_time and account.wallets.asof == current_time
            callbacks.append(
                {
                    "at": current_time.isoformat(),
                    "known": known.isoformat(),
                    "last_label": visible.iloc[-1].date.isoformat(),
                }
            )

        account.strategylist[0].bot_loop_start = observe
        try:
            account.start()
        except Exception as exc:
            if split_case != "fractional_reject":
                raise
            from freqtrade.data.history.equity import EquityAdmissionError

            assert isinstance(exc, EquityAdmissionError) and "Fractional" in str(exc)
            trades = list(Trade.get_trades_proxy())
            assert len(trades) == 1 and trades[0].amount == 6 and trades[0].stake_amount == 600
            assert account.wallets.asof == plan.closes[0]
            assert len(trades[0].orders) == 1 and trades[0].orders[0].safe_filled == 6
            assert not list((output / "backtest_results").glob("*.zip"))
            save(
                output / "expected_rejection.json",
                {
                    "expected": True,
                    "error": str(exc),
                    "trade_amount": trades[0].amount,
                    "stake_amount": trades[0].stake_amount,
                    "wallet_asof": account.wallets.asof,
                    "orders": [
                        {
                            "id": o.order_id,
                            "quantity": o.safe_filled,
                            "price": o.safe_price,
                            "at": o.order_filled_utc,
                        }
                        for o in trades[0].orders
                    ],
                    "native_start_attempt": True,
                    "completed": False,
                    "historical_accounts": 0,
                },
            )
            print(market, split_case, "EXPECTED FRACTIONAL REJECTION", flush=True)
            return
        assert split_case != "fractional_reject"
        assert account.equity_lifecycle_completed and account.equity_net_result_completed
        save(output / "equity_net_result.json", account.equity_net_result)
        save(
            output / "dividend_journal.json", [asdict(e) for e in account.wallets.dividend_journal]
        )
        result = account.all_bt_content[account.strategy.get_strategy_name()]
        account.open_snapshots = account.equity_open_snapshots
        account.close_snapshots = account.equity_close_snapshots
        save(output / "equity_fixture_result.json", account.equity_fixture_result)
        zips = list((output / "backtest_results").glob("*.zip"))
        assert len(zips) == 1
        save(
            output / "native_export_receipt.json",
            [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in zips],
        )
        orders = [
            {
                "trade_id": t.id,
                "pair": t.pair,
                "order_id": o.order_id,
                "at": o.order_filled_utc.isoformat(),
                "side": o.ft_order_side,
                "price": o.safe_price,
                "quantity": o.safe_filled,
                "is_open": t.is_open,
            }
            for t in Trade.get_trades_proxy()
            for o in t.orders
            if o.safe_filled
        ]
        assert all(o["quantity"] % 1 == 0 for o in orders)
        assert len(account.open_snapshots) == len(account.close_snapshots) == len(plan.opens)
        record = {
            "market": market,
            "synthetic_only": True,
            "native_constructor": True,
            "native_backtest": True,
            "native_start": True,
            "orders": orders,
            "opens": account.open_snapshots,
            "closes": account.close_snapshots,
            "native_closed_trade_count": len(result["results"]),
            "native_trade_stats_are_overlay_gross": True,
            "historical_accounts": 0,
            "factor_analysis_calls": 0,
            "bench_records": 0,
            "future_extrema": future_extrema,
            "terminal_held_fixture": terminal_held,
            "split_case": split_case,
            "callbacks": callbacks,
            "split_contract": asdict(splits),
            "trades": [
                {
                    "id": t.id,
                    "amount": t.amount,
                    "stake_amount": t.stake_amount,
                    "open_rate": t.open_rate,
                    "open_trade_value": t.open_trade_value,
                    "is_open": t.is_open,
                    "close_profit_abs": t.close_profit_abs,
                    "class_name": type(t).__name__,
                    "unit_journal": getattr(t, "unit_journal", ()),
                }
                for t in Trade.get_trades_proxy()
            ],
            "dividend_contract": asdict(dividend_contract),
            "cash_contract": asdict(cash),
            "fee_contract": asdict(fee_contract),
            "terminal": "held shares at exact close, no force sale",
        }
        for p, digest in bound.items():
            assert hashlib.sha256(Path(p).read_bytes()).hexdigest() == digest
        save(output / "native_trace.json", record)
        print(
            market,
            "NATIVE LOOP",
            len(orders),
            "fills;",
            len(plan.opens),
            "sessions; 0 historical accounts",
            flush=True,
        )
        return record
    finally:
        if account is not None:
            account.cleanup()
        exchange.close()
        LocalTrade.reset_trades()
        Trade.use_db = old


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["XNYS", "XHKG"], required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument(
        "--split-case",
        choices=[
            "default",
            "none",
            "forward_exit",
            "reverse_exit",
            "forward_held",
            "thirds_held",
            "same_open_buy",
            "forward_reverse",
            "future2",
            "future4",
            "fractional_reject",
        ],
        required=True,
    )
    parser.add_argument("--future-extrema", action="store_true")
    parser.add_argument("--terminal-held", action="store_true")
    args = parser.parse_args()

    def offline(*a, **kw):
        raise RuntimeError("Synthetic native integration is offline")

    socket.socket.connect = offline
    run(
        args.market,
        HERE / "runs" / args.attempt,
        split_case=args.split_case,
        future_extrema=args.future_extrema,
        terminal_held=True,
    )
