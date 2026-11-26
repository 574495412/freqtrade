"""Read-only split revision lookup on the inherited native DataProvider clock."""

from freqtrade.data.equity_provider import EquityDataProvider
from freqtrade.data.equity_split_notices import SplitNoticeBook
from freqtrade.data.history.equity import EquityAdmissionError


class EquitySplitNoticeDataProvider(EquityDataProvider):
    def __init__(self, config, schedules, notices):
        super().__init__(config, schedules)
        if not isinstance(notices, SplitNoticeBook):
            raise EquityAdmissionError("Explicit immutable notice history required")
        for notice in notices.notices:
            if (
                notice.pair not in schedules
                or schedules[notice.pair].contract.calendar != notice.calendar
            ):
                raise EquityAdmissionError("Notice must bind an exact covered pair/calendar")
        self._split_notices = notices

    def get_split_notice(self, pair, security_id, event_id):
        self._check(pair, "1d")
        if self._equity_asof is None:
            raise EquityAdmissionError("Native callback observation clock required")
        return self._split_notices.lookup(pair, security_id, event_id, at=self._equity_asof)
