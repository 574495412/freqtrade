"""Native IStrategy/DataProvider ownership of diagnostic scores; finance closed."""
import hashlib
import json
from pathlib import Path

import pandas as pd

from freqtrade.data.fip_path import daily_fip_components, hourly_completed_fip_view
from freqtrade.enums import RunMode
from freqtrade.strategy import IStrategy


class FipInputs(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = '1h'
    startup_candle_count = 0
    can_short = True
    minimal_roi = {}
    stoploss = -.99

    def bot_start(self, **kwargs):
        if (self.config.get('research_diagnostic_only') is not True
                or self.config.get('dry_run') is not True
                or self.config.get('runmode') != RunMode.BACKTEST):
            raise ValueError('FIP input diagnostic only; no account or service')
        path = Path(self.config['research_admission'])
        admission = json.loads(path.read_text())
        if (hashlib.sha256(path.read_bytes()).hexdigest() != self.config['research_admission_sha256']
                or self.config['exchange']['pair_whitelist'] != admission['pairs']):
            raise ValueError('Frozen FIP inputs/universe changed')
        for row in admission['native_inputs']:
            if hashlib.sha256(Path(row['path']).read_bytes()).hexdigest() != row['sha256']:
                raise ValueError('FIP native source bytes changed')
        data = {}
        for pair in admission['pairs']:
            frame = self.dp.get_pair_dataframe(pair, '1d', 'futures').set_index('date')
            data[pair] = frame.close
        self.research_close = pd.DataFrame(data)
        self.research_components = daily_fip_components(self.research_close)

    def ft_bot_start(self, **kwargs):
        raise ValueError('FIP financial lifecycle not admitted by input diagnostic')

    def populate_indicators(self, dataframe, metadata):
        parts = hourly_completed_fip_view(self.research_components, pd.DatetimeIndex(dataframe.date))
        for name, frame in parts.items():
            dataframe[name] = frame[metadata['pair']].to_numpy()
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe['enter_long'] = 0
        dataframe['enter_short'] = 0
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe['exit_long'] = 0
        dataframe['exit_short'] = 0
        return dataframe

    def confirm_trade_entry(self, *args, **kwargs):
        return False

    def bot_loop_start(self, *args, **kwargs):
        raise ValueError('Input diagnostics cannot become all-cash account evidence')
