"""Real native initialization and backtest loop on explicit synthetic inputs.

Never start a financial account. The full stock start/load/result lifecycle
remains closed. Native default trade statistics are not fee-overlay net NAV.
"""
import argparse
import hashlib
import json
import socket
import shutil
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, datetime, UTC
from pathlib import Path

from bootstrap import HERE, ROOT, PARENT, boot

boot()
from test_sessions import routed
from freqtrade.commands.optimize_commands import setup_optimize_configuration
from freqtrade.data.equity_provider import EquityDataProvider
from freqtrade.enums import RunMode
from freqtrade.equity_dividend_wallets import EquityDividendWallets
from freqtrade.equity_wallets import SettlementDay
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.equity_fee_execution import EquityFeeFillMixin
from freqtrade.optimize.equity_nav import EquityMark, capture_equity_nav
from freqtrade.optimize.equity_open_only import EquityOpenOnlyMixin
from freqtrade.optimize.equity_fixture_lifecycle import EquityFixtureLifecycleMixin
from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
from freqtrade.optimize.equity_sessions import EquitySessionRoutingMixin
from freqtrade.persistence import LocalTrade, Trade
from freqtrade.resolvers import ExchangeResolver


class OpenProbe(EquityFixtureLifecycleMixin, EquityOpenOnlyMixin, EquitySessionRoutingMixin, EquityFeeFillMixin, Backtesting):
    pass


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, default=str, allow_nan=False) + '\n')


def run(market, output, *, future_extrema=False, terminal_held=False):
    old = Trade.use_db
    Trade.use_db = False
    LocalTrade.reset_trades()
    reference, pair, source = routed(market)
    if future_extrema:
        source['high'], source['low'] = source.open * 100, source.open * .001
    if terminal_held:
        source.loc[source.index[-3:], 'close'] = 101.
    plan, original_wallet = reference.equity_session_plan, reference.wallets
    # A settlement tail is not a longer trading/evaluation window. Keep the
    # originally missing-tail failure; add synthetic cash release dates only.
    cash = replace(original_wallet.contract, settlement_days=original_wallet.contract.settlement_days + tuple(
        SettlementDay(date(2025, 1, d), datetime(2025, 1, d, 22 if market == 'XNYS' else 10, tzinfo=UTC))
        for d in (13, 14)))
    fee_contract = replace(original_wallet.fees, cash=cash)
    dividend_contract = replace(original_wallet.dividends, cash=cash)
    raw = json.loads((ROOT / 'user_data/crypto_quant/copula_fills_native_20261115_v4/fixtures/normal1000/config.json').read_text())
    raw.update(strategy='OpenClockProbe', strategy_path=str(PARENT), timeframe='1d', timeframe_detail='',
               dry_run_wallet=1000, stake_amount=600, stake_currency=plan.currency,
               max_open_trades=1, tradable_balance_ratio=1., fee=0., trading_mode='spot',
               margin_mode='', position_adjustment_enable=False, trailing_stop=False,
               minimal_roi={}, stoploss=-.99, synthetic_equity_fixture=True,
               equity_execution_model='completed_close_next_open_v1',
               user_data_dir=str(output), datadir=str(output), db_url='sqlite://')
    raw['timerange'] = '20250102-20250111'
    raw['backtest_cache'] = 'none'
    raw['export'] = 'trades'
    def config_candle():
        from freqtrade.enums import CandleType
        return CandleType.SPOT
    raw['exchange'].update(pair_whitelist=[pair], pair_blacklist=[], key='', secret='', password='')
    raw['pairlists'] = [{'method': 'StaticPairList'}]
    output.mkdir(parents=True, exist_ok=False)
    save(output / 'config.json', raw)
    source.to_feather(output / 'synthetic_ohlcv.feather')
    FeatherDataHandler(output).ohlcv_store(pair, '1d', source, candle_type=config_candle())
    bound = {}
    for p in list(HERE.glob('*.py')) + list((ROOT / 'freqtrade/optimize').glob('equity*.py')) + [HERE / 'protocol.json', output / 'synthetic_ohlcv.feather', output / 'config.json']:
        bound[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    save(output / 'implementation_freeze.json', bound)
    snapshot_manifest = {}
    for path, digest in bound.items():
        p = Path(path)
        if p.is_relative_to(output):
            continue
        target = output / 'source_snapshot' / p.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)
        snapshot_manifest[path] = {'sha256': digest, 'snapshot': str(target)}
    save(output / 'source_snapshot_manifest.json', snapshot_manifest)
    config = setup_optimize_configuration({'config': [str(output / 'config.json')], 'datadir': str(output)}, RunMode.BACKTEST)
    exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
    template = json.loads((ROOT / 'user_data/crypto_quant/copula_fills_native_20261115_v4/fixtures/normal1000/synthetic_markets_maintenance_fixed.json').read_text())
    meta = deepcopy(next(iter(template['markets'].values())))
    meta.update(id='ABC'+plan.currency, symbol=pair, base='ABC', quote=plan.currency,
                baseId='ABC', quoteId=plan.currency, settle=None, settleId=None,
                type='spot', spot=True, swap=False, future=False, contract=False,
                linear=None, inverse=None, contractSize=None, active=True)
    meta['precision'] = {'amount': 1., 'price': .01}
    meta['limits']['amount'] = {'min': 1., 'max': 1e9}
    meta['limits']['cost'] = {'min': 1., 'max': 1e12}
    currencies = {c: {'id': c, 'code': c, 'precision': 8} for c in ['ABC', plan.currency]}
    exchange._markets = {pair: meta}
    exchange._api.set_markets({pair: meta}, currencies)
    exchange._api_async.set_markets({pair: meta}, currencies)
    exchange.validate_config(config)
    save(output / 'synthetic_market.json', {pair: meta})
    save(output / 'synthetic_contracts.json', dict(cash=asdict(cash), fees=asdict(fee_contract), dividends=asdict(dividend_contract), execution=asdict(replace(reference.equity_execution_contract, cash=cash))))
    account = None
    try:
        account = OpenProbe(config, exchange=exchange)
        account.equity_session_plan = plan
        account.equity_execution_contract = replace(reference.equity_execution_contract, cash=cash)
        account.dataprovider = EquityDataProvider(config, reference.dataprovider.schedules)
        account.wallets = EquityDividendWallets(config, exchange, fees=fee_contract,
            dividends=dividend_contract, asof=plan.opens[0])
        callbacks = []
        def observe(current_time, **kwargs):
            visible, known = account.dataprovider.get_analyzed_dataframe(pair, '1d')
            assert known < current_time and account.wallets.asof == current_time
            callbacks.append(dict(at=current_time.isoformat(), known=known.isoformat(), last_label=visible.iloc[-1].date.isoformat()))
        account.strategylist[0].bot_loop_start = observe
        account.start()
        assert account.equity_lifecycle_completed
        result = account.all_bt_content[account.strategy.get_strategy_name()]
        account.open_snapshots = account.equity_open_snapshots
        account.close_snapshots = account.equity_close_snapshots
        save(output / 'equity_fixture_result.json', account.equity_fixture_result)
        zips = list((output / 'backtest_results').glob('*.zip'))
        assert len(zips) == 1
        save(output / 'native_export_receipt.json', [{'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in zips])
        orders = [dict(trade_id=t.id, pair=t.pair, order_id=o.order_id,
                       at=o.order_filled_utc.isoformat(), side=o.ft_order_side,
                       price=o.safe_price, quantity=o.safe_filled, is_open=t.is_open)
                  for t in Trade.get_trades_proxy() for o in t.orders if o.safe_filled]
        assert all(o['price'] == 100 and o['quantity'] % 1 == 0 for o in orders)
        assert len(account.open_snapshots) == len(account.close_snapshots) == len(plan.opens)
        record = dict(market=market, synthetic_only=True, native_constructor=True,
            native_backtest=True, native_start=True, orders=orders,
            opens=account.open_snapshots, closes=account.close_snapshots,
            native_closed_trade_count=len(result['results']),
            native_trade_stats_are_overlay_gross=True, historical_accounts=0,
            factor_analysis_calls=0, bench_records=0, future_extrema=future_extrema,
            terminal_held_fixture=terminal_held, callbacks=callbacks,
            cash_contract=asdict(cash), fee_contract=asdict(fee_contract),
            terminal='held shares at exact close, no force sale')
        for p, digest in bound.items():
            assert hashlib.sha256(Path(p).read_bytes()).hexdigest() == digest
        save(output / 'native_trace.json', record)
        print(market, 'NATIVE LOOP', len(orders), 'fills;', len(plan.opens), 'sessions; 0 historical accounts', flush=True)
        return record
    finally:
        if account is not None:
            account.cleanup()
        exchange.close()
        LocalTrade.reset_trades()
        Trade.use_db = old


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--market', choices=['XNYS', 'XHKG'], required=True)
    parser.add_argument('--attempt', required=True)
    parser.add_argument('--future-extrema', action='store_true')
    parser.add_argument('--terminal-held', action='store_true')
    args = parser.parse_args()
    def offline(*a, **kw):
        raise RuntimeError('Synthetic native integration is offline')
    socket.socket.connect = offline
    run(args.market, HERE / 'runs' / args.attempt, future_extrema=args.future_extrema, terminal_held=args.terminal_held)
