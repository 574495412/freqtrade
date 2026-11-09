"""Local historical input adapter for the native dynamic pairlist lifecycle."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from freqtrade.enums import RunMode
from freqtrade.exceptions import OperationalException
from freqtrade.plugins.pairlist.IPairList import IPairList, SupportsBacktesting


class HistoricalVolumePairList(IPairList):
    is_pairlist_generator = True
    supports_backtesting = SupportsBacktesting.YES

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self._config["runmode"] != RunMode.BACKTEST:
            raise OperationalException("Historical universe is backtest only")
        blob = Path(self._pairlistconfig["schedule"]).read_bytes()
        if hashlib.sha256(blob).hexdigest() != self._pairlistconfig["sha256"]:
            raise OperationalException("Historical schedule checksum differs")
        schedule = json.loads(blob)
        self._union = schedule["union"]
        self._days = schedule["days"]
        self._day = None
        self._selected = None
        for day, row in self._days.items():
            decision = datetime.fromisoformat(day).replace(tzinfo=UTC)
            if (
                datetime.fromisoformat(row["latest_input_close"]) >= decision
                or len(row["pairs"]) != 80
                or len(set(row["pairs"])) != 80
                or not set(row["pairs"]).issubset(self._union)
            ):
                raise OperationalException("Invalid/noncausal historical schedule")

    @property
    def needstickers(self):
        return False

    def short_desc(self):
        return "Previous complete UTC day turnover, historical top80"

    @staticmethod
    def description():
        return "Local causal historical schedule, native account execution."

    def gen_pairlist(self, tickers):
        clock = self._pairlistmanager._dataprovider.backtest_time
        if clock is None:
            return self._union.copy()  # Native initial data loading, never an entry decision.
        day = clock.astimezone(UTC).date().isoformat()
        if day not in self._days:
            raise OperationalException("No admitted schedule at replay date " + day)
        if self._day != day:
            self._day = day
            self._selected = self._days[day]["pairs"]
        return self._selected.copy()
