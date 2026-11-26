"""Read-only macro access on the owning Freqtrade DataProvider clock."""

from freqtrade.data.dataprovider import DataProvider
from freqtrade.data.macro_transmission import MacroClock, MacroInputError, macro_headwind_at
from freqtrade.enums import RunMode


class MacroTransmissionDataProvider(DataProvider):
    """Research-only supported provider subclass; no financial account admission."""

    def __init__(self, config, yield_quotes, dollar_quotes, *, clock=MacroClock.SNAPSHOT_OBSERVED):
        if (
            config.get("runmode") != RunMode.BACKTEST
            or config.get("research_macro_inputs") is not True
        ):
            raise MacroInputError("Isolated backtest input research required")
        super().__init__(config, None)
        self._macro_sources = yield_quotes, dollar_quotes
        self._macro_clock_mode = MacroClock(clock)
        self._macro_at = None

    def _set_dataframe_max_date(self, limit_date):
        # Native parent remains the owner of ordinary candle slicing.
        from freqtrade.data.macro_transmission import _aware

        self._macro_at = _aware(limit_date)
        return super()._set_dataframe_max_date(limit_date)

    def macro_headwind(self):
        if self._macro_at is None:
            raise MacroInputError("Native callback observation clock required")
        return macro_headwind_at(
            *self._macro_sources, at=self._macro_at, clock=self._macro_clock_mode
        )

    def clear_cache(self):
        super().clear_cache()
        self._macro_at = None
