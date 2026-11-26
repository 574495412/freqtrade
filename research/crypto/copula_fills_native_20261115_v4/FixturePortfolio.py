"""Registered synthetic symbols only; never a source20 financial candidate."""
import hashlib
from pathlib import Path

import pandas as pd
from CopulaPortfolio import CopulaPortfolio

from freqtrade.enums import RunMode


class FixtureCopula(CopulaPortfolio):
    def ft_bot_start(self, **kwargs):
        if self.config.get('runmode') != RunMode.BACKTEST or self.config.get('dry_run') is not True:
            raise ValueError('Fixture is isolated backtest-only')
        if self.config['exchange']['pair_whitelist'] != ['FAKEONE/USDT:USDT', 'FAKETWO/USDT:USDT']:
            raise ValueError('Fixture cannot enable real source securities')
        self.bot_start()
        self.ft_load_hyper_params(False)

    def bot_start(self, **kwargs):
        path = Path(self.config['fixture_forecasts'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != self.config['fixture_forecasts_sha256']:
            raise ValueError('Synthetic forecasts changed')
        # Same frozen parent helper, synthetic states/prices explicitly isolated.
        import importlib.util
        source = Path(self.config['research_root']) / 'freqtrade/data/copula_execution.py'
        spec = importlib.util.spec_from_file_location('fixture_copula_helper', source)
        owner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(owner)
        self.forecasts = pd.read_parquet(path)
        self.research_intents = owner.intent_schedule(self.forecasts, self.entry_alpha)
        self._signals = {pair: {'entry': {}, 'exit': {}} for pair in self.config['exchange']['pair_whitelist']}
        for intent in self.research_intents:
            if intent['action'] == 'budget': continue
            signal_at = intent['at'] - pd.Timedelta(minutes=5)
            for index, (symbol, _) in enumerate(intent['legs']):
                side = int(intent['state']) * (-1 if index == 0 else 1)
                self._signals[symbol + '/USDT:USDT'][intent['action']][signal_at] = side
        self._execution_setup()


class FixtureRejectSecond(FixtureCopula):
    def confirm_trade_entry(self, pair, **kwargs):
        if pair == 'FAKETWO/USDT:USDT':
            return False
        return super().confirm_trade_entry(pair=pair, **kwargs)
