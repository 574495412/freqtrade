"""Fresh real native constructor/strategy initialization; never calls start."""
import argparse
import importlib.util
import json
import shutil
import socket
import sys
from pathlib import Path

HERE = Path(__file__).absolute().parent


def main(name, attempt):
    plan = json.loads((HERE / 'plan_v2.json').read_text())
    sys.path.insert(0, plan['engine_source'])
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize.backtesting import Backtesting
    from freqtrade.resolvers import ExchangeResolver
    candidate = next(c for c in plan['candidates'] if c['name'] == name)
    out = Path(plan['output_root']) / ('preflight_' + name + '_' + attempt)
    out.mkdir(exist_ok=False)
    strategies = out / 'strategies'
    strategies.mkdir()
    for relative, row in candidate['source_files'].items():
        shutil.copyfile(row['path'], strategies / relative)
    raw = json.loads(Path(candidate['config']['path']).read_text())
    raw.update(datadir=plan['datadir'], user_data_dir=str(out), strategy_path=str(strategies),
               timerange='20250101-20260901', db_url='sqlite://', export='none')
    (out / 'config.json').write_text(json.dumps(raw, indent=2) + '\n')
    config = setup_optimize_configuration({'config': [str(out / 'config.json')], 'datadir': plan['datadir']}, RunMode.BACKTEST)
    exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
    market = json.loads(Path(candidate['market_snapshot']['path']).read_text())
    exchange._markets, exchange._leverage_tiers = market['markets'], market['leverage_tiers']
    exchange._api.set_markets(market['markets'], market['currencies'])
    exchange._api_async.set_markets(market['markets'], market['currencies'])
    exchange.validate_config(config)
    def offline(*args, **kwargs):
        raise RuntimeError('Native admission is offline')
    socket.socket.connect = offline
    source = Path(plan['price_precision_source']['path'])
    spec = importlib.util.spec_from_file_location('matched_precision', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class Probe(module.MatchedPricePrecisionMixin, Backtesting):
        pass
    account = None
    try:
        account = Probe(config, exchange=exchange)
        account.setup_frozen_price_precision(pairs=tuple(candidate['pairs']))
        strategy = account.strategylist[0]
        account._set_strategy(strategy)
        strategy.research_weights.to_parquet(out / 'native_weights.parquet')
        data, _ = account.load_bt_data()
        analyzed = strategy.advise_all_indicators(data)
        for pair, frame in analyzed.items():
            flags = strategy.ft_advise_signals(frame, {'pair': pair})
            flags.to_parquet(out / (pair.split('/')[0] + '_native_signals.parquet'), index=False)
        (out / 'receipt.json').write_text(json.dumps(dict(passed=True, native_constructor=True,
            native_strategy_initialization=True, native_signal_frames=2, native_weight_rows=len(strategy.research_weights),
            financial_start=False, native_trades=0, compatibility_tick_flag='research_matched_nfi only opts into existing fixed tick adapter,not NFI strategy')) + '\n')
        print(name, 'real native admission passed;0financialstarts')
    finally:
        if account is not None:
            account.cleanup()
        exchange.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--attempt', required=True)
    args = parser.parse_args()
    main(args.candidate, args.attempt)
