"""Actual native IStrategy signal adaptation, deliberately guarded from finance."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pandas as pd

from freqtrade.enums import RunMode
from freqtrade.strategy import IStrategy


class ExecutionInputs(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = '5m'
    startup_candle_count = 0
    can_short = True
    minimal_roi = {}
    stoploss = -.99
    entry_alpha = 10

    def ft_bot_start(self, **kwargs):
        raise ValueError('Copula paired execution preparation only; funded admission remains false')

    def bot_start(self, **kwargs):
        if not self.config.get('research_diagnostic_only') or self.config.get('runmode') != RunMode.BACKTEST:
            raise ValueError('Isolated backtest diagnostic lifecycle required')
        root = Path(self.config['research_root'])
        path = root / 'freqtrade/data/copula_execution.py'
        spec = importlib.util.spec_from_file_location('copula_execution_v3', path)
        owner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(owner)
        frozen = json.loads((root / 'user_data/crypto_quant/copula_account_native_20261115_v2/model_capsule.json').read_text())
        record = frozen['forecasts']
        if hashlib.sha256(Path(record['path']).read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('Frozen forecast changed')
        expected = ['BTC','ETH','BCH','XRP','EOS','LTC','TRX','ETC','LINK','XLM','ADA','XMR','DASH','ZEC','XTZ','ATOM','BNB','ONT','IOTA','BAT']
        if self.config['exchange']['pair_whitelist'] != [s + '/USDT:USDT' for s in expected]:
            raise ValueError('Full original20 source whitelist required')
        self.forecasts = pd.read_parquet(record['path'])
        self.research_intents = owner.intent_schedule(self.forecasts, self.entry_alpha)
        self._signals = {pair: {'entry': {}, 'exit': {}} for pair in self.config['exchange']['pair_whitelist']}
        for intent in self.research_intents:
            if intent['action'] == 'budget':
                continue
            signal_at = intent['at'] - pd.Timedelta(minutes=5)
            for index, (symbol, _) in enumerate(intent['legs']):
                side = int(intent['state']) * (-1 if index == 0 else 1)
                pair = symbol + '/USDT:USDT'
                values = self._signals[pair][intent['action']]
                if signal_at in values:
                    raise ValueError('Conflicting signal intent on one native candle')
                values[signal_at] = side

    def populate_indicators(self, dataframe, metadata):
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        values = self._signals[metadata['pair']]['entry']
        sides = dataframe.date.map(values).fillna(0)
        dataframe['enter_long'] = sides.eq(1).astype(int)
        dataframe['enter_short'] = sides.eq(-1).astype(int)
        dataframe['enter_tag'] = 'copula_hourly_fixed_week_units'
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        values = self._signals[metadata['pair']]['exit']
        sides = dataframe.date.map(values).fillna(0)
        dataframe['exit_long'] = sides.eq(1).astype(int)
        dataframe['exit_short'] = sides.eq(-1).astype(int)
        dataframe['exit_tag'] = 'copula_neutral_or_week_boundary'
        return dataframe


class ExecutionInputs15(ExecutionInputs):
    entry_alpha = 15


class ExecutionInputs20(ExecutionInputs):
    entry_alpha = 20
