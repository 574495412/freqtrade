"""No indicator calculation/backtest: config paths and frozen metadata bootstrap."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from runtime import ROOT, environment_guard, install_offline_exchange

HERE = Path(__file__).resolve().parent


class BootstrapTest(unittest.TestCase):
    def test_two_public_configs_parse_and_exchange_uses_saved_rules(self):
        manifest = json.loads((HERE / 'diagnostic_manifest.json').read_text())
        guard, _ = environment_guard(manifest)
        from freqtrade.commands.arguments import Arguments
        from freqtrade.configuration import setup_utils_configuration
        from freqtrade.enums import RunMode
        from freqtrade.resolvers import ExchangeResolver
        for market, spec in manifest['markets'].items():
            with self.subTest(market=market), tempfile.TemporaryDirectory(prefix='rd-causality-bootstrap-') as temp:
                root = Path(temp)
                userdir = root / 'utility-userdir'
                userdir.mkdir()
                config = copy.deepcopy(json.loads((ROOT / spec['config_path']).read_text()))
                config['export'] = 'none'
                config['backtest_cache'] = 'none'
                config_path = root / 'config.json'
                config_path.write_text(json.dumps(config))
                args = Arguments(['lookahead-analysis', '--config', str(config_path),
                    '--strategy-path', str((ROOT / manifest['strategy_path']).parent),
                    '--strategy', 'NostalgiaForInfinityX8', '--datadir', str(ROOT / spec['datadir']),
                    '--userdir', str(userdir), '--timerange', manifest['timerange'],
                    '--pairs', *spec['pairs'], '--minimum-trade-amount', '5', '--targeted-trade-amount', '20']).get_parsed_arg()
                parsed = setup_utils_configuration(args, RunMode.UTIL_NO_EXCHANGE)
                self.assertEqual(parsed['user_data_dir'], userdir)
                self.assertEqual(parsed['runmode'], RunMode.UTIL_NO_EXCHANGE)
                restore = install_offline_exchange(guard, spec['market_snapshot'])
                try:
                    exchange = ExchangeResolver.load_exchange(parsed, load_leverage_tiers=True)
                    expected = json.loads((ROOT / spec['market_snapshot']['path']).read_text())
                    self.assertEqual(exchange._markets, expected['markets'])
                    self.assertEqual(exchange._leverage_tiers, expected['leverage_tiers'])
                    exchange.close()
                finally:
                    restore()


if __name__ == '__main__':
    unittest.main()
