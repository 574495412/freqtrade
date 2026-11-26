"""Local cross-project Registry bridge using actual native IStrategy callbacks.

Scores are conditional quote diagnostics, never funded entry/exit decisions.
Market cohorts are constructed after explicit raw-history admission, before any
cross-sectional computation. Invalid columns cannot contaminate valid scores.
"""

import numpy as np
import pandas as pd

from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.strategy import IStrategy


class RegistryFeatures:
    def __init__(self, registry, provider, pairs, factor_ids, universe):
        if not pairs or len(pairs) != len(set(pairs)) or not factor_ids:
            raise EquityAdmissionError("Unique nonempty cohort and factor identities required")
        self.pairs, self.factor_ids = tuple(pairs), tuple(factor_ids)
        self.provider, self.registry = provider, registry
        frames = {p: provider.historic_ohlcv(p, "1d") for p in pairs}
        schedules = [provider.schedules[p] for p in pairs]
        if len({s.contract.calendar for s in schedules}) != 1:
            raise EquityAdmissionError("Cross-market feature cohorts are forbidden")
        self.dates = pd.DatetimeIndex(frames[pairs[0]].date)
        self.panel = {}
        for field in ["open", "high", "low", "close", "volume"]:
            self.panel[field] = pd.DataFrame(
                {p: f[field].to_numpy() for p, f in frames.items()}, index=self.dates
            )
        if any(not pd.DatetimeIndex(f.date).equals(self.dates) for f in frames.values()):
            raise EquityAdmissionError("Every native session grid must agree")
        self.scores = {}
        for identity in factor_ids:
            if universe not in registry.get(identity).meta["universe"]:
                raise EquityAdmissionError(f"{identity}: market outside owning metadata")
            result = registry.compute(identity, self.panel)
            if not result.index.equals(self.dates) or list(result.columns) != list(pairs):
                raise EquityAdmissionError("Registry changed native input axes")
            if np.isinf(result.to_numpy()).any():
                raise EquityAdmissionError("Infinite scores are unsupported")
            self.scores[identity] = result

    def attach(self, frame, pair):
        if pair not in self.pairs or not pd.DatetimeIndex(frame.date).equals(self.dates):
            raise EquityAdmissionError(
                "Indicator attachment requires exact pair and full native axes"
            )
        for field in self.panel:
            if not np.array_equal(frame[field].to_numpy(), self.panel[field][pair].to_numpy()):
                raise EquityAdmissionError(
                    "Indicator input differs from Registry source prices/volume"
                )
        result = frame.copy()
        for identity, score in self.scores.items():
            result[identity] = score[pair].to_numpy()
        return result

    def visible(self, identity, pair):
        if identity not in self.scores or pair not in self.pairs:
            raise EquityAdmissionError("Undeclared factor or pair")
        frame = pd.DataFrame({"date": self.dates, "score": self.scores[identity][pair].to_numpy()})
        at = self.provider._equity_asof
        if at is None:
            raise EquityAdmissionError("Explicit native callback clock required")
        return self.provider.schedules[pair].visible(frame, at)


class RegistrySignal(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1d"
    startup_candle_count = 0
    can_short = False
    minimal_roi = {}
    stoploss = -0.99

    def bot_start(self, **kwargs):
        if self.config.get("equity_diagnostic_only") is not True:
            raise EquityAdmissionError(
                "Complete historical equity financial account remains closed"
            )

    def populate_indicators(self, dataframe, metadata):
        return self.registry_features.attach(dataframe, metadata["pair"])

    def populate_entry_trend(self, dataframe, metadata):
        dataframe["enter_long"] = 0
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = 0
        return dataframe

    def bot_loop_start(self, **kwargs):
        raise EquityAdmissionError("Diagnostic only; no funded loop")

    def confirm_trade_entry(self, *args, **kwargs):
        return False
