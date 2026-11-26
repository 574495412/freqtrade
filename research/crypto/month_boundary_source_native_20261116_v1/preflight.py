"""Actual native hourly conversion proves known-calendar execution alignment."""
import argparse
import importlib.util
import json
import shutil
import socket
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).absolute().parent


def main(name):
    plan = json.loads((HERE / 'plan.json').read_text())
    sys.path.insert(0, plan['engine_source'])
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize.backtesting import Backtesting, HEADERS
    from freqtrade.resolvers import ExchangeResolver
    candidate = next(c for c in plan['candidates'] if c['name'] == name)
    out = Path(plan['output_root']) / ('preflight_' + name)
    out.mkdir(exist_ok=False)
    strategies = out / 'strategies'
    strategies.mkdir()
    for filename, row in candidate['source_files'].items():
        shutil.copyfile(row['path'], strategies / filename)
    raw = json.loads(Path(candidate['config']['path']).read_text())
    raw.update(datadir=plan['datadir'], user_data_dir=str(out), strategy_path=str(strategies),
               timerange='2024123123-20260901', db_url='sqlite://', export='none')
    # Native parser uses exact seconds for an hourly left boundary.
    raw['timerange'] = f"{int(pd.Timestamp(plan['native_start']).timestamp())}-{int(pd.Timestamp(plan['end']).timestamp())-1}"
    (out / 'config.json').write_text(json.dumps(raw, indent=2) + '\n')
    config = setup_optimize_configuration({'config': [str(out / 'config.json')], 'datadir': plan['datadir']}, RunMode.BACKTEST)
    exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
    market = json.loads(Path(candidate['market_snapshot']['path']).read_text())
    exchange._markets, exchange._leverage_tiers = market['markets'], market['leverage_tiers']
    exchange._api.set_markets(market['markets'], market['currencies'])
    exchange._api_async.set_markets(market['markets'], market['currencies'])
    exchange.validate_config(config)
    def offline(*args, **kwargs):
        raise RuntimeError('Native calendar admission is offline')
    socket.socket.connect = offline
    spec = importlib.util.spec_from_file_location('matched_precision', plan['price_precision_source']['path'])
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
        data, _ = account.load_bt_data()
        processed = strategy.advise_all_indicators(data)
        converted = account._get_ohlcv_as_lists(processed)
        grid = pd.date_range(plan['start'], plan['comparison_end'], freq='h').as_unit('ns')
        checks = []
        for pair, values in converted.items():
            frame = pd.DataFrame(values, columns=HEADERS)
            frame = frame.loc[frame.date.ge(pd.Timestamp(plan['start'])) & frame.date.le(pd.Timestamp(plan['comparison_end']))]
            assert pd.DatetimeIndex(frame.date).as_unit('ns').equals(grid)
            expected = []
            for stamp in grid:
                day = stamp.date()
                if strategy.hold:
                    expected.append(1)
                else:
                    import calendar
                    expected.append(int(day.day <= strategy.first or day.day > calendar.monthrange(day.year, day.month)[1] - strategy.last))
            assert frame.enter_long.tolist() == expected
            assert frame.exit_long.tolist() == [1 - value for value in expected]
            assert not frame.enter_short.any() and not frame.exit_short.any()
            frame.to_parquet(out / (pair.split('/')[0] + '_native_converted.parquet'), index=False)
            checks.append(dict(pair=pair, rows=len(frame), calendar_flags_exact_after_native_lag=True,
                               first_execution_clock=str(frame.date.iloc[0])))
        (out / 'receipt.json').write_text(json.dumps(dict(passed=True, native_constructor=True,
             native_strategy_init=True, native_conversion=True, checks=checks,
             funded_accounts=0, financial_start=False), indent=2) + '\n')
        print(name, 'native calendar execution flags passed;0financialstarts')
    finally:
        if account is not None:
            account.cleanup()
        exchange.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', required=True)
    main(parser.parse_args().candidate)
