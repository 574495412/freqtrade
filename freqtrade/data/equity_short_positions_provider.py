"""Read-only SFC report lookup through the existing native equity provider clock."""

from datetime import date
from re import fullmatch

from freqtrade.data.equity_provider import EquityDataProvider
from freqtrade.data.equity_short_positions import reported_position
from freqtrade.data.history.equity import EquityAdmissionError


class EquityShortPositionDataProvider(EquityDataProvider):
    def __init__(self, config, schedules, reports):
        super().__init__(config, schedules)
        self._short_reports = {}
        for report in reports:
            if report.reporting_date in self._short_reports:
                raise EquityAdmissionError(
                    "Report revisions require a separately evidenced contract"
                )
            self._short_reports[report.reporting_date] = report

    def get_reported_short_position(self, pair, reporting_date):
        self._check(pair, "1d")
        if self._equity_asof is None:
            raise EquityAdmissionError("Native callback clock required")
        contract = self.schedules[pair].contract
        if contract.calendar != "XHKG" or not fullmatch(r"[0-9]{5}/HKD", pair):
            raise EquityAdmissionError("Exact HKD original Hong Kong counter required")
        if type(reporting_date) is not date:
            raise EquityAdmissionError("Explicit reporting date required; no stale fallback")
        return reported_position(
            self._short_reports.get(reporting_date), pair.split("/")[0], at=self._equity_asof
        )
